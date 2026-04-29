"""verify_chat node — runs Tier 1 + Tier 2 over facts and narrative.

Two distinct guarantees:
- Each fact in ``parsed_facts`` runs the same chain the brief uses
  (``verify_items``). Failed facts are dropped silently.
- The narrative runs ``check_narrative_grounding``, which fails the turn
  if a number/date in prose isn't in any fact's verbatim_excerpts, or if
  the narrative trips the advisory denylist. On narrative failure the
  prose is replaced with a sanitized fallback; verified facts are kept
  so the physician still sees the safe parts of the answer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.schemas.brief import BriefItemType
from oe_ai_agent.verifier import verify_items
from oe_ai_agent.verifier.narrative import check_narrative_grounding

VerifyChatNode = Callable[[ChatState], Awaitable[dict[str, object]]]

_FALLBACK_NARRATIVE = (
    "I can't ground my prose answer cleanly. The fact cards below are "
    "verified — try a more specific question (name a lab, medication, "
    "or date) for a written summary."
)


def make_verify_chat_node(
    allowed_types: frozenset[BriefItemType] | None = None,
) -> VerifyChatNode:
    types = allowed_types if allowed_types is not None else frozenset(BriefItemType)

    async def verify_chat_node(state: ChatState) -> dict[str, object]:
        fact_result = verify_items(
            state.parsed_facts,
            state.cached_context,
            expected_patient_uuid=state.patient_uuid,
            allowed_types=types,
        )
        verified_facts = fact_result.verified
        failures = list(fact_result.failures)

        narrative_failure = check_narrative_grounding(
            state.parsed_narrative, verified_facts
        )
        if narrative_failure is not None:
            failures.append(narrative_failure)
            return {
                "verified_facts": verified_facts,
                "verification_failures": failures,
                "parsed_narrative": _FALLBACK_NARRATIVE,
            }

        return {
            "verified_facts": verified_facts,
            "verification_failures": failures,
        }

    return verify_chat_node
