"""ensure_chat_context node — no eager chart prefetch for chat.

The brief agent needs broad context. Chat is user-directed, so the LLM
should choose targeted tools from the catalog. This node preserves cached
tool rows from prior turns but intentionally performs no FHIR reads on a
new conversation.
"""

from __future__ import annotations

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.observability import step


async def ensure_chat_context_node(state: ChatState) -> dict[str, object]:
    async with step("ensure_chat_context") as record:
        record.attrs.update(
            {
                "cache_hit": bool(state.cached_context),
                "row_count": len(state.cached_context),
                "eager_prefetch": False,
            }
        )
    return {}
