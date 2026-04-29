"""verify node — runs Tier 1 + Tier 2 deterministic checks."""

from __future__ import annotations

from oe_ai_agent.agent.state import AgentState
from oe_ai_agent.verifier import verify_items


async def verify_node(state: AgentState) -> dict[str, object]:
    result = verify_items(
        state.parsed_items,
        state.tool_results,
        expected_patient_uuid=state.patient_uuid,
    )
    return {
        "verified_items": result.verified,
        "verification_failures": result.failures,
    }
