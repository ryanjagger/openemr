"""LiteLLM-backed implementation of LlmClient.

Provider-agnostic on paper but currently exercised only with Anthropic
(``claude-sonnet-4-6``). The graph never imports this directly — selection
happens in ``main._llm_client()`` via ``LLM_PROVIDER``.

Notes:
* JSON-mode is requested through ``response_format``; LiteLLM translates
  per-provider. Anthropic implements this via tool-use under the hood.
  ``parse_output`` is the safety net if a provider produces non-JSON.
* Prompt caching for the system prompt is a follow-up — small payload at
  MVP traffic levels.
"""

from __future__ import annotations

import json
from typing import Any

import litellm

from oe_ai_agent.llm.client import LlmChatResult, LlmToolCall

DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"


class LiteLLMClient:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        max_tokens: int = 1024,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_tokens

    @property
    def model_id(self) -> str:
        return self._model

    async def chat(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None = None,
    ) -> str:
        kwargs = self._base_kwargs(messages)
        if response_format is not None:
            kwargs["response_format"] = response_format

        response = await litellm.acompletion(**kwargs)
        choices = response["choices"]
        content = choices[0]["message"].get("content")
        if isinstance(content, str) and content.strip():
            return content
        # Some providers return content as a list of parts; flatten to text.
        if isinstance(content, list):
            text_parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            joined = "".join(text_parts).strip()
            if joined:
                return joined
        # If we got here the model produced no text content; surface as JSON
        # null body so parse_output can record a parse_error rather than crash.
        return json.dumps({"items": []})

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LlmChatResult:
        kwargs = self._base_kwargs(messages)
        if tools:
            kwargs["tools"] = tools
        if response_format is not None:
            kwargs["response_format"] = response_format

        response = await litellm.acompletion(**kwargs)
        message = response["choices"][0]["message"]
        return LlmChatResult(
            content=_extract_text(message.get("content")),
            tool_calls=_extract_tool_calls(message.get("tool_calls") or []),
        )

    def _base_kwargs(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
        }
        if self._api_key is not None:
            kwargs["api_key"] = self._api_key
        return kwargs


def _extract_text(content: object) -> str | None:
    if isinstance(content, str):
        stripped = content.strip()
        return stripped or None
    if isinstance(content, list):
        text_parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        joined = "".join(text_parts).strip()
        return joined or None
    return None


def _extract_tool_calls(raw_calls: list[Any]) -> list[LlmToolCall]:
    calls: list[LlmToolCall] = []
    for raw in raw_calls:
        if not isinstance(raw, dict):
            continue
        function = raw.get("function") or {}
        name = function.get("name")
        if not isinstance(name, str):
            continue
        arguments_raw = function.get("arguments")
        arguments: dict[str, Any]
        if isinstance(arguments_raw, str):
            try:
                arguments = json.loads(arguments_raw) if arguments_raw else {}
            except json.JSONDecodeError:
                arguments = {}
        elif isinstance(arguments_raw, dict):
            arguments = arguments_raw
        else:
            arguments = {}
        tool_call_id = raw.get("id") or raw.get("tool_call_id") or ""
        if not isinstance(tool_call_id, str):
            tool_call_id = str(tool_call_id)
        calls.append(LlmToolCall(tool_call_id=tool_call_id, name=name, arguments=arguments))
    return calls
