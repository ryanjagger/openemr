"""verify node — runs Tier 1 + Tier 2 deterministic checks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from oe_ai_agent.agent.state import AgentState
from oe_ai_agent.observability import step
from oe_ai_agent.schemas.brief import BriefItemType
from oe_ai_agent.verifier import verify_items

VerifyNode = Callable[[AgentState], Awaitable[dict[str, object]]]


def make_verify_node(
    allowed_types: frozenset[BriefItemType] | None = None,
) -> VerifyNode:
    types = allowed_types if allowed_types is not None else frozenset(BriefItemType)

    async def verify_node(state: AgentState) -> dict[str, object]:
        async with step("verify") as record:
            result = verify_items(
                state.parsed_items,
                state.tool_results,
                expected_patient_uuid=state.patient_uuid,
                allowed_types=types,
            )
            record.attrs.update(
                {
                    "verified_count": len(result.verified),
                    "failure_count": len(result.failures),
                }
            )
        return {
            "verified_items": result.verified,
            "verification_failures": result.failures,
        }

    return verify_node
