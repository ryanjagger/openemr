"""LiteLLM-backed implementation of LlmClient.

Provider-agnostic on paper but currently exercised only with Anthropic
(``claude-sonnet-4-6``). The graph never imports this directly — selection
happens in ``main._llm_client()`` via ``LLM_PROVIDER``.

Notes:
* JSON-mode is requested through ``response_format``; LiteLLM translates
  per-provider. Anthropic implements this via tool-use under the hood.
  ``parse_output`` is the safety net if a provider produces non-JSON.
* Usage (tokens, cost, latency) is captured for every call so the audit
  log and admin viewer can answer the four observability questions.
"""

from __future__ import annotations

import json
import time
from typing import Any

import litellm

from oe_ai_agent.llm.client import (
    LlmChatResult,
    LlmCompletionResult,
    LlmToolCall,
    LlmUsage,
)
from oe_ai_agent.observability.cost import compute_completion_cost

DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096


class LiteLLMClient:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
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
    ) -> LlmCompletionResult:
        kwargs = self._base_kwargs(messages)
        if response_format is not None:
            kwargs["response_format"] = response_format

        started = time.monotonic_ns()
        response = await litellm.acompletion(**kwargs)
        latency_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        usage = _extract_usage(response, latency_ms)

        choices = response["choices"]
        content = choices[0]["message"].get("content")
        if isinstance(content, str) and content.strip():
            return LlmCompletionResult(content=content, usage=usage)
        # Some providers return content as a list of parts; flatten to text.
        if isinstance(content, list):
            text_parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            joined = "".join(text_parts).strip()
            if joined:
                return LlmCompletionResult(content=joined, usage=usage)
        # If we got here the model produced no text content; surface as JSON
        # null body so parse_output can record a parse_error rather than crash.
        return LlmCompletionResult(content=json.dumps({"items": []}), usage=usage)

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

        started = time.monotonic_ns()
        response = await litellm.acompletion(**kwargs)
        latency_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        usage = _extract_usage(response, latency_ms)

        message = response["choices"][0]["message"]
        content = _attr_or_key(message, "content")
        return LlmChatResult(
            content=_extract_text(content),
            tool_calls=[
                *_extract_tool_calls(_attr_or_key(message, "tool_calls") or []),
                *_extract_anthropic_content_tool_calls(content),
            ],
            usage=usage,
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
            _attr_or_key(part, "text") or ""
            for part in content
            if _attr_or_key(part, "type") == "text"
        ]
        joined = "".join(text_parts).strip()
        return joined or None
    return None


def _extract_tool_calls(raw_calls: list[Any]) -> list[LlmToolCall]:
    calls: list[LlmToolCall] = []
    for raw in raw_calls:
        function = _attr_or_key(raw, "function") or {}
        name = _attr_or_key(function, "name")
        if not isinstance(name, str):
            continue
        arguments_raw = _attr_or_key(function, "arguments")
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
        tool_call_id = _attr_or_key(raw, "id") or _attr_or_key(raw, "tool_call_id") or ""
        if not isinstance(tool_call_id, str):
            tool_call_id = str(tool_call_id)
        calls.append(LlmToolCall(tool_call_id=tool_call_id, name=name, arguments=arguments))
    return calls


def _extract_anthropic_content_tool_calls(content: object) -> list[LlmToolCall]:
    """Extract Anthropic-native tool_use blocks from LiteLLM message content.

    Some Anthropic responses are normalized into OpenAI-style
    ``message.tool_calls``. Others keep the native content shape:
    ``[{"type": "tool_use", "id": "...", "name": "...", "input": {...}}]``.
    Without this path the graph sees ``tool_calls=[]`` and ``content=None``.
    """
    if not isinstance(content, list):
        return []

    calls: list[LlmToolCall] = []
    for part in content:
        if _attr_or_key(part, "type") != "tool_use":
            continue
        name = _attr_or_key(part, "name")
        if not isinstance(name, str):
            continue
        raw_arguments = _attr_or_key(part, "input")
        arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
        raw_id = _attr_or_key(part, "id") or _attr_or_key(part, "tool_use_id") or ""
        tool_call_id = raw_id if isinstance(raw_id, str) else str(raw_id)
        calls.append(
            LlmToolCall(
                tool_call_id=tool_call_id,
                name=name,
                arguments=arguments,
            )
        )
    return calls


def _extract_usage(response: Any, latency_ms: int) -> LlmUsage:
    """Pull token counts from the response and compute cost.

    LiteLLM normalizes ``usage`` to OpenAI's shape (``prompt_tokens``,
    ``completion_tokens``, ``total_tokens``). Some providers omit it, in
    which case we record latency only.
    """
    usage_obj = _attr_or_key(response, "usage")
    prompt_tokens = _coerce_int(_attr_or_key(usage_obj, "prompt_tokens"))
    completion_tokens = _coerce_int(_attr_or_key(usage_obj, "completion_tokens"))
    total_tokens = _coerce_int(_attr_or_key(usage_obj, "total_tokens"))
    if total_tokens == 0 and (prompt_tokens or completion_tokens):
        total_tokens = prompt_tokens + completion_tokens
    cost_usd = compute_completion_cost(response)
    return LlmUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )


def _attr_or_key(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _coerce_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
