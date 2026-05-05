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
class LlmUsage:
    """Per-call usage metrics emitted alongside every LLM response.

    Latency is wall-clock around the provider call. Cost is best-effort via
    ``litellm.completion_cost``; missing pricing tables yield 0.0 rather
    than blowing up. Mock client returns a zeroed instance so the rest of
    the pipeline doesn't have to special-case None.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0

    def merge(self, other: LlmUsage) -> LlmUsage:
        """Sum two usages (used to aggregate across tool-loop iterations)."""
        return LlmUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            latency_ms=self.latency_ms + other.latency_ms,
        )


@dataclass(frozen=True)
class LlmChatResult:
    """Bundle of content + tool calls a multi-turn agent step produced.

    The model may return either: (a) only ``content`` (a final envelope),
    (b) only ``tool_calls`` (it needs more data first), or (c) both — some
    providers stream tool calls alongside intermediate prose.
    """

    content: str | None
    tool_calls: list[LlmToolCall] = field(default_factory=list)
    usage: LlmUsage = field(default_factory=LlmUsage)


@dataclass(frozen=True)
class LlmCompletionResult:
    """Single-shot completion result with attached usage."""

    content: str
    usage: LlmUsage = field(default_factory=LlmUsage)


class LlmClient(Protocol):
    @property
    def model_id(self) -> str:
        """Identifier of the model the client routes to (e.g. ``mock`` or
        ``anthropic/claude-sonnet-4-6``). Surfaced into the audit log."""
        ...

    async def chat(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
    ) -> LlmCompletionResult:
        """Return the assistant's reply plus usage metrics."""
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
