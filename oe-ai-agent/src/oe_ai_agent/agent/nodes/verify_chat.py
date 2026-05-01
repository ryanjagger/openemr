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
from datetime import UTC, datetime, timedelta

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.observability import step, update_langfuse_observation
from oe_ai_agent.schemas.brief import VerificationFailure
from oe_ai_agent.schemas.chat import ChatFact, ChatFactType
from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.verifier.constraints import (
    ADVISORY_DENYLIST,
    CHAT_ALLOWED_TABLES_FOR_TYPE,
    CHAT_MAX_AGE_DAYS_FOR_TYPE,
)
from oe_ai_agent.verifier.narrative import check_narrative_grounding
from oe_ai_agent.verifier.tier1_structural import (
    check_citations_exist,
    check_patient_binding,
    check_typed_fact_reextraction,
)

VerifyChatNode = Callable[[ChatState], Awaitable[dict[str, object]]]

_FALLBACK_NARRATIVE = (
    "I can't ground my prose answer cleanly. The fact cards below are "
    "verified — try a more specific question (name a lab, medication, "
    "or date) for a written summary."
)


def make_verify_chat_node(
    allowed_types: frozenset[ChatFactType] | None = None,
) -> VerifyChatNode:
    types = allowed_types if allowed_types is not None else frozenset(ChatFactType)

    async def verify_chat_node(state: ChatState) -> dict[str, object]:
        async with step("verify_chat") as record:
            verified_facts, failures = _verify_chat_facts(
                state.parsed_facts,
                state.cached_context,
                expected_patient_uuid=state.patient_uuid,
                allowed_types=types,
            )

            narrative_failure = check_narrative_grounding(
                state.parsed_narrative, verified_facts
            )
            record.attrs.update(
                {
                    "verified_count": len(verified_facts),
                    "failure_count": len(failures),
                    "narrative_grounded": narrative_failure is None,
                }
            )
            if narrative_failure is not None:
                failures.append(narrative_failure)
                record.attrs["narrative_failure_rule"] = narrative_failure.rule
                update_langfuse_observation(
                    output={
                        "verified_facts": [
                            fact.model_dump(mode="json") for fact in verified_facts
                        ],
                        "failures": [
                            failure.model_dump(mode="json") for failure in failures
                        ],
                        "narrative": _FALLBACK_NARRATIVE,
                    }
                )
                return {
                    "verified_facts": verified_facts,
                    "verification_failures": failures,
                    "parsed_narrative": _FALLBACK_NARRATIVE,
                }

            update_langfuse_observation(
                output={
                    "verified_facts": [
                        fact.model_dump(mode="json") for fact in verified_facts
                    ],
                    "failures": [failure.model_dump(mode="json") for failure in failures],
                    "narrative": state.parsed_narrative,
                }
            )
            return {
                "verified_facts": verified_facts,
                "verification_failures": failures,
            }

    return verify_chat_node


def _verify_chat_facts(
    facts: list[ChatFact],
    tool_rows: list[TypedRow],
    expected_patient_uuid: str,
    *,
    allowed_types: frozenset[ChatFactType],
    now: datetime | None = None,
) -> tuple[list[ChatFact], list[VerificationFailure]]:
    verified: list[ChatFact] = []
    failures: list[VerificationFailure] = []
    moment = now or datetime.now(tz=UTC)

    for index, fact in enumerate(facts):
        failure = (
            _check_disabled_type(fact, allowed_types, item_index=index)
            or _check_citation_floor(fact, item_index=index)
            or _check_advisory_denylist(fact, item_index=index)
            or check_citations_exist(fact, tool_rows, item_index=index)
            or check_patient_binding(fact, tool_rows, expected_patient_uuid, item_index=index)
            or _check_type_table_compatibility(fact, item_index=index)
            or check_typed_fact_reextraction(fact, tool_rows, item_index=index)
            or _check_staleness(fact, tool_rows, moment, item_index=index)
        )
        if failure is None:
            verified.append(fact)
        else:
            failures.append(failure)

    return verified, failures


def _check_disabled_type(
    fact: ChatFact,
    allowed_types: frozenset[ChatFactType],
    item_index: int,
) -> VerificationFailure | None:
    if fact.type in allowed_types:
        return None
    return VerificationFailure(
        rule="tier2_type_disabled",
        detail=f"type {fact.type.value!r} is disabled in this deployment",
        item_index=item_index,
    )


def _check_citation_floor(
    fact: ChatFact,
    item_index: int,
) -> VerificationFailure | None:
    if fact.citations:
        return None
    return VerificationFailure(
        rule="tier2_citation_floor",
        detail="item has no citations",
        item_index=item_index,
    )


def _check_advisory_denylist(
    fact: ChatFact,
    item_index: int,
) -> VerificationFailure | None:
    match = ADVISORY_DENYLIST.search(fact.text)
    if match is None:
        return None
    return VerificationFailure(
        rule="tier2_advisory_denylist",
        detail=f"text contains denied advisory phrase {match.group(0)!r}",
        item_index=item_index,
    )


def _check_type_table_compatibility(
    fact: ChatFact,
    item_index: int,
) -> VerificationFailure | None:
    allowed = CHAT_ALLOWED_TABLES_FOR_TYPE.get(fact.type, frozenset())
    for citation in fact.citations:
        if citation.resource_type not in allowed:
            return VerificationFailure(
                rule="tier1_type_table_compatibility",
                detail=(
                    f"item type {fact.type.value} cannot cite "
                    f"{citation.resource_type}; allowed: {sorted(allowed)}"
                ),
                item_index=item_index,
            )
    return None


def _check_staleness(
    fact: ChatFact,
    tool_rows: list[TypedRow],
    now: datetime,
    item_index: int,
) -> VerificationFailure | None:
    max_age_days = CHAT_MAX_AGE_DAYS_FOR_TYPE.get(fact.type)
    if max_age_days is None:
        return None
    rows_by_id = {row.resource_id: row for row in tool_rows}
    cited_rows = [rows_by_id[c.resource_id] for c in fact.citations if c.resource_id in rows_by_id]
    if not cited_rows:
        return None
    youngest = max(row.last_updated for row in cited_rows)
    if youngest.tzinfo is None:
        youngest = youngest.replace(tzinfo=UTC)
    if now - youngest > timedelta(days=max_age_days):
        return VerificationFailure(
            rule="tier1_staleness",
            detail=(
                f"youngest citation {youngest.isoformat()} is older than "
                f"{max_age_days} days for type {fact.type.value}"
            ),
            item_index=item_index,
        )
    return None
