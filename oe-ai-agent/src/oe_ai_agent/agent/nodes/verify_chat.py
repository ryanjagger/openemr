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
from oe_ai_agent.schemas.chat import ChatFact, ChatFactType, SourceProvenance
from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.status import update_current_chat_status
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
_GUIDELINE_FALLBACK_NARRATIVE = (
    "I found guideline evidence in the verified source cards below."
)
_EMPTY_ANSWER_FALLBACK_NARRATIVE = (
    "I couldn't find enough chart evidence to answer that question."
)
_MIN_SOURCE_MATCH_CHARS = 3


def make_verify_chat_node(
    allowed_types: frozenset[ChatFactType] | None = None,
) -> VerifyChatNode:
    types = allowed_types if allowed_types is not None else frozenset(ChatFactType)

    async def verify_chat_node(state: ChatState) -> dict[str, object]:
        async with step("verify_chat") as record:
            update_current_chat_status(stage="Verifying response grounding")
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
            update_current_chat_status(
                stage="Verification complete",
                detail=f"{len(verified_facts)} facts verified",
                attrs={
                    "verified_count": len(verified_facts),
                    "failure_count": len(failures),
                    "narrative_grounded": narrative_failure is None,
                },
            )
            if narrative_failure is not None:
                if _only_guideline_facts(verified_facts):
                    record.attrs["narrative_failure_rule"] = narrative_failure.rule
                    record.attrs["narrative_failure_suppressed"] = True
                    update_langfuse_observation(
                        output={
                            "verified_facts": [
                                fact.model_dump(mode="json")
                                for fact in verified_facts
                            ],
                            "failures": [
                                failure.model_dump(mode="json") for failure in failures
                            ],
                            "suppressed_narrative_failure": (
                                narrative_failure.model_dump(mode="json")
                            ),
                            "narrative": _GUIDELINE_FALLBACK_NARRATIVE,
                        }
                    )
                    return {
                        "verified_facts": verified_facts,
                        "verification_failures": failures,
                        "parsed_narrative": _GUIDELINE_FALLBACK_NARRATIVE,
                    }

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

            if not state.parsed_narrative.strip() and not verified_facts:
                failure = VerificationFailure(
                    rule="empty_answer",
                    detail="model returned no narrative and no verified facts",
                )
                failures.append(failure)
                record.attrs["empty_answer"] = True
                record.attrs["failure_count"] = len(failures)
                update_current_chat_status(
                    stage="No grounded answer produced",
                    detail=_EMPTY_ANSWER_FALLBACK_NARRATIVE,
                    attrs={"failure_count": len(failures)},
                )
                update_langfuse_observation(
                    output={
                        "verified_facts": [],
                        "failures": [
                            failure.model_dump(mode="json") for failure in failures
                        ],
                        "narrative": _EMPTY_ANSWER_FALLBACK_NARRATIVE,
                    }
                )
                return {
                    "verified_facts": [],
                    "verification_failures": failures,
                    "parsed_narrative": _EMPTY_ANSWER_FALLBACK_NARRATIVE,
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


def _only_guideline_facts(facts: list[ChatFact]) -> bool:
    return bool(facts) and all(fact.type is ChatFactType.GUIDELINE for fact in facts)


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
            verified.append(_with_source_provenance(fact, tool_rows))
        else:
            failures.append(failure)

    return verified, failures


def _with_source_provenance(fact: ChatFact, tool_rows: list[TypedRow]) -> ChatFact:
    sources = _source_provenance_for_fact(fact, tool_rows)
    if not sources:
        return fact
    return fact.model_copy(update={"source_provenance": sources})


def _source_provenance_for_fact(
    fact: ChatFact,
    tool_rows: list[TypedRow],
) -> list[SourceProvenance]:
    rows_by_key = {(row.resource_type, row.resource_id): row for row in tool_rows}
    haystack = _source_match_haystack(fact)
    sources: list[SourceProvenance] = []
    seen: set[tuple[str, str, str, int | None, str | None, str | None]] = set()

    for citation in fact.citations:
        row = rows_by_key.get((citation.resource_type, citation.resource_id))
        if row is None:
            continue

        direct = _source_from_provenance(row.fields.get("aiProvenance"), row)
        if direct is not None:
            _append_unique_source(sources, seen, direct)

        for source in _matching_item_sources(row, haystack):
            _append_unique_source(sources, seen, source)

    return sources


def _append_unique_source(
    sources: list[SourceProvenance],
    seen: set[tuple[str, str, str, int | None, str | None, str | None]],
    source: SourceProvenance,
) -> None:
    key = (
        source.resource_type,
        source.resource_id,
        source.document_id,
        source.page,
        source.link_id,
        source.snippet,
    )
    if key in seen:
        return
    seen.add(key)
    sources.append(source)


def _matching_item_sources(row: TypedRow, haystack: str) -> list[SourceProvenance]:
    items = row.fields.get("item")
    all_sources: list[SourceProvenance] = []
    matched_sources: list[SourceProvenance] = []
    _collect_item_sources(row, items, haystack, all_sources, matched_sources)
    if matched_sources:
        return matched_sources
    if len(all_sources) == 1:
        return all_sources
    return []


def _collect_item_sources(
    row: TypedRow,
    items: object,
    haystack: str,
    all_sources: list[SourceProvenance],
    matched_sources: list[SourceProvenance],
) -> None:
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        link_id = _optional_string(item.get("linkId"))
        source = _source_from_provenance(
            item.get("aiProvenance"),
            row,
            link_id=link_id,
        )
        if source is not None:
            all_sources.append(source)
            if _item_source_matches_fact(item, source, haystack):
                matched_sources.append(source)

        nested = item.get("item")
        if isinstance(nested, list):
            _collect_item_sources(row, nested, haystack, all_sources, matched_sources)


def _source_from_provenance(
    value: object,
    row: TypedRow,
    *,
    link_id: str | None = None,
) -> SourceProvenance | None:
    if not isinstance(value, dict):
        return None
    document_id = _document_id(value.get("documentId") or value.get("document_id"))
    if document_id is None:
        return None

    return SourceProvenance(
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        document_id=document_id,
        page=_optional_int(value.get("page")),
        bbox=value.get("bbox"),
        snippet=_optional_string(value.get("snippet")),
        confidence=_optional_float(value.get("confidence")),
        model=_optional_string(value.get("model")),
        link_id=link_id,
    )


def _source_match_haystack(fact: ChatFact) -> str:
    parts = [fact.text, *fact.verbatim_excerpts]
    return _normalize_source_text("\n".join(part for part in parts if part))


def _item_source_matches_fact(
    item: dict[object, object],
    source: SourceProvenance,
    haystack: str,
) -> bool:
    if not haystack:
        return False
    candidates = [source.snippet, _optional_string(item.get("text"))]
    candidates.extend(_answer_strings(item.get("answer")))
    for candidate in candidates:
        normalized = _normalize_source_text(candidate or "")
        if len(normalized) < _MIN_SOURCE_MATCH_CHARS:
            continue
        if normalized in haystack or haystack in normalized:
            return True
    return False


def _answer_strings(answer: object) -> list[str]:
    if not isinstance(answer, list):
        return []
    values: list[str] = []
    for entry in answer:
        if not isinstance(entry, dict):
            continue
        for key, value in entry.items():
            if not isinstance(key, str) or not key.startswith("value"):
                continue
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, int | float | bool):
                values.append(str(value))
            elif isinstance(value, dict):
                text = value.get("text")
                if isinstance(text, str):
                    values.append(text)
    return values


def _normalize_source_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _document_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


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
    rows_by_key = {(row.resource_type, row.resource_id): row for row in tool_rows}
    cited_rows = [
        rows_by_key[(c.resource_type, c.resource_id)]
        for c in fact.citations
        if (c.resource_type, c.resource_id) in rows_by_key
    ]
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
