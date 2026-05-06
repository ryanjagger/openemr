"""Tier 1 structural verifier rules — deterministic, no LLM.

Each rule returns ``None`` when the item passes, or a
``VerificationFailure`` when it does not. The verifier orchestrator drops
items whose first failing rule resolves to a ``VerificationFailure``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

from oe_ai_agent.schemas.brief import BriefItem, Citation, VerificationFailure
from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.verifier.constraints import (
    ALLOWED_TABLES_FOR_TYPE,
    GLOBAL_EVIDENCE_RESOURCE_TYPES,
    MAX_AGE_DAYS_FOR_TYPE,
)


class CitationBackedText(Protocol):
    @property
    def text(self) -> str: ...

    @property
    def citations(self) -> list[Citation]: ...


def check_citations_exist(
    item: CitationBackedText,
    tool_rows: list[TypedRow],
    item_index: int | None = None,
) -> VerificationFailure | None:
    """Every cited ResourceType/id pair must appear in the rows the model saw."""
    seen_keys = {_row_key(row) for row in tool_rows}
    for citation in item.citations:
        if _citation_key(citation) not in seen_keys:
            return VerificationFailure(
                rule="tier1_citations_exist",
                detail=(
                    f"citation {citation.resource_type}/{citation.resource_id} "
                    "not in tool_results"
                ),
                item_index=item_index,
            )
    return None


def check_patient_binding(
    item: CitationBackedText,
    tool_rows: list[TypedRow],
    expected_patient_uuid: str,
    item_index: int | None = None,
) -> VerificationFailure | None:
    """Each cited row's patient_id must equal the request's patient_uuid."""
    rows_by_key = {_row_key(row): row for row in tool_rows}
    for citation in item.citations:
        row = rows_by_key.get(_citation_key(citation))
        if row is None:
            continue  # citation existence is a separate rule
        if row.resource_type in GLOBAL_EVIDENCE_RESOURCE_TYPES:
            continue
        if row.patient_id != expected_patient_uuid:
            return VerificationFailure(
                rule="tier1_patient_binding",
                detail=(
                    f"citation {citation.resource_id} bound to patient "
                    f"{row.patient_id}, expected {expected_patient_uuid}"
                ),
                item_index=item_index,
            )
    return None


def check_type_table_compatibility(
    item: BriefItem,
    item_index: int | None = None,
) -> VerificationFailure | None:
    """citation.resource_type must be in ALLOWED_TABLES_FOR_TYPE[item.type]."""
    allowed = ALLOWED_TABLES_FOR_TYPE.get(item.type, frozenset())
    for citation in item.citations:
        if citation.resource_type not in allowed:
            return VerificationFailure(
                rule="tier1_type_table_compatibility",
                detail=(
                    f"item type {item.type.value} cannot cite "
                    f"{citation.resource_type}; allowed: {sorted(allowed)}"
                ),
                item_index=item_index,
            )
    return None


_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def check_typed_fact_reextraction(
    item: CitationBackedText,
    tool_rows: list[TypedRow],
    item_index: int | None = None,
) -> VerificationFailure | None:
    """Numbers and dates in item.text must appear verbatim in some cited row.

    Catches digit transposition (creatinine 1.8 vs 8.1) and date drift.
    Resource ID strings inside item.text are ignored — those are citation
    payloads, not clinical claims.
    """
    if not item.citations:
        return None  # citation count check handles empty case
    rows_by_key = {_row_key(row): row for row in tool_rows}
    cited_rows = [
        rows_by_key[_citation_key(c)]
        for c in item.citations
        if _citation_key(c) in rows_by_key
    ]
    if not cited_rows:
        return None  # citation existence handles this

    cited_blob = " ".join(_serialize_row_for_match(row) for row in cited_rows)
    cited_numbers = _numbers_in(cited_blob)
    cited_resource_ids = {row.resource_id for row in cited_rows}

    for date_match in _DATE_RE.findall(item.text):
        if date_match not in cited_blob:
            return VerificationFailure(
                rule="tier1_typed_fact_reextraction",
                detail=f"date {date_match!r} not found in cited rows",
                item_index=item_index,
            )

    for number_match in _NUMBER_RE.findall(item.text):
        # Skip numbers that appear inside a cited resource_id; those are not facts.
        if any(number_match in rid for rid in cited_resource_ids):
            continue
        if number_match not in cited_blob and not _number_equivalent(
            number_match,
            cited_numbers,
        ):
            return VerificationFailure(
                rule="tier1_typed_fact_reextraction",
                detail=f"number {number_match!r} not found in cited rows",
                item_index=item_index,
            )
    return None


def check_staleness(
    item: BriefItem,
    tool_rows: list[TypedRow],
    now: datetime,
    item_index: int | None = None,
) -> VerificationFailure | None:
    """Drop items whose youngest cited row is older than MAX_AGE_DAYS_FOR_TYPE."""
    max_age_days = MAX_AGE_DAYS_FOR_TYPE.get(item.type)
    if max_age_days is None:
        return None
    rows_by_key = {_row_key(row): row for row in tool_rows}
    cited_rows = [
        rows_by_key[_citation_key(c)]
        for c in item.citations
        if _citation_key(c) in rows_by_key
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
                f"{max_age_days} days for type {item.type.value}"
            ),
            item_index=item_index,
        )
    return None


def _serialize_row_for_match(row: TypedRow) -> str:
    """Flatten a TypedRow into a string suitable for substring matching."""
    parts: list[str] = [row.resource_id]
    if row.verbatim_excerpt:
        parts.append(row.verbatim_excerpt)
    parts.append(_stringify(row.fields))
    return " ".join(parts)


def _row_key(row: TypedRow) -> tuple[str, str]:
    return row.resource_type, row.resource_id


def _citation_key(citation: Citation) -> tuple[str, str]:
    return citation.resource_type, citation.resource_id


def _stringify(value: object) -> str:
    if isinstance(value, dict):
        return " ".join(_stringify(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_stringify(v) for v in value)
    return str(value)


def _numbers_in(text: str) -> list[str]:
    return _NUMBER_RE.findall(text)


def _number_equivalent(candidate: str, references: list[str]) -> bool:
    try:
        candidate_decimal = Decimal(candidate)
    except InvalidOperation:
        return False

    for reference in references:
        try:
            if candidate_decimal == Decimal(reference):
                return True
        except InvalidOperation:
            continue
    return False
