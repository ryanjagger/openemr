"""ensure_context node — turn 1 runs fetch_context; later turns reuse the cache.

The conversation store is the source of truth. The endpoint hydrates
``state.cached_context`` from the store before invoking the graph; this
node only fires the FHIR pre-fetch when that hydrated list is empty.
"""

from __future__ import annotations

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.agent.nodes.fetch_context import fetch_context
from oe_ai_agent.tools import FhirClient


async def ensure_context_node(state: ChatState) -> dict[str, object]:
    if state.cached_context:
        return {}
    async with FhirClient(
        base_url=state.fhir_base_url,
        bearer_token=state.bearer_token.get_secret_value(),
        request_id=state.request_id,
    ) as client:
        result = await fetch_context(client, state.patient_uuid)
    return {
        "cached_context": result.rows,
        "fetch_errors": result.errors,
    }
