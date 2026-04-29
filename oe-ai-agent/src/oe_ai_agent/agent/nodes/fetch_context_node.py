"""LangGraph wrapper around the existing fetch_context helper."""

from __future__ import annotations

from oe_ai_agent.agent.nodes.fetch_context import fetch_context
from oe_ai_agent.agent.state import AgentState
from oe_ai_agent.observability import step
from oe_ai_agent.tools import FhirClient


async def fetch_context_node(state: AgentState) -> dict[str, object]:
    async with step("fetch_context") as record:
        async with FhirClient(
            base_url=state.fhir_base_url,
            bearer_token=state.bearer_token.get_secret_value(),
            request_id=state.request_id,
        ) as client:
            result = await fetch_context(client, state.patient_uuid)
        record.attrs.update(
            {"row_count": len(result.rows), "error_count": len(result.errors)}
        )

    return {
        "tool_results": result.rows,
        "fetch_errors": result.errors,
    }
