"""LLM client protocol used by the ``llm_call`` node.

The graph never imports a concrete provider. Phase 3a uses
``MockLlmClient``; Phase 3b swaps in a LiteLLM-backed implementation.
"""

from __future__ import annotations

from typing import Any, Protocol


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
