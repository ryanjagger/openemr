"""LLM client protocol used by the agent nodes.

Two entry points:

* ``chat()`` — single-shot text completion used by the brief agent. Returns
  the assistant's reply as a string.
* ``chat_with_tools()`` — used by the chat agent. Accepts tool schemas,
  returns an ``LlmChatResult`` carrying either an envelope JSON, a list of
  tool calls the model wants executed, or both.

The graph never imports a concrete provider. Selection happens in
``main._llm_client()`` via ``LLM_PROVIDER``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class LlmToolCall:
    """One tool invocation requested by the model."""

    tool_call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LlmChatResult:
    """Bundle of content + tool calls a multi-turn agent step produced.

    The model may return either: (a) only ``content`` (a final envelope),
    (b) only ``tool_calls`` (it needs more data first), or (c) both — some
    providers stream tool calls alongside intermediate prose.
    """

    content: str | None
    tool_calls: list[LlmToolCall] = field(default_factory=list)


class LlmClient(Protocol):
    @property
    def model_id(self) -> str:
        """Identifier of the model the client routes to (e.g. ``mock`` or
        ``anthropic/claude-sonnet-4-6``). Surfaced into the audit log."""
        ...

    async def chat(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None = None,
    ) -> str:
        """Return the assistant's reply as a string. JSON-mode responses
        are returned as the raw JSON string for ``parse_output`` to validate.
        """
        ...

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LlmChatResult:
        """Multi-turn entry point with tool-calling support."""
        ...
