"""Deterministic verifier — runs after parse_output, before render."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from oe_ai_agent.schemas.brief import BriefItem, BriefItemType, VerificationFailure
from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.verifier.tier1_structural import (
    check_citations_exist,
    check_patient_binding,
    check_staleness,
    check_type_table_compatibility,
    check_typed_fact_reextraction,
)
from oe_ai_agent.verifier.tier2_schema import (
    check_advisory_denylist,
    check_citation_floor,
    check_disabled_type,
)


@dataclass(frozen=True)
class VerificationResult:
    verified: list[BriefItem]
    failures: list[VerificationFailure]


def verify_items(
    items: list[BriefItem],
    tool_rows: list[TypedRow],
    expected_patient_uuid: str,
    *,
    now: datetime | None = None,
    allowed_types: frozenset[BriefItemType] | None = None,
) -> VerificationResult:
    """Run Tier 1 + Tier 2. First failing rule per item drops the item."""
    moment = now or datetime.now(tz=UTC)
    types = allowed_types if allowed_types is not None else frozenset(BriefItemType)
    verified: list[BriefItem] = []
    failures: list[VerificationFailure] = []

    for index, item in enumerate(items):
        failure = (
            check_disabled_type(item, types, item_index=index)
            or check_citation_floor(item, item_index=index)
            or check_advisory_denylist(item, item_index=index)
            or check_citations_exist(item, tool_rows, item_index=index)
            or check_patient_binding(item, tool_rows, expected_patient_uuid, item_index=index)
            or check_type_table_compatibility(item, item_index=index)
            or check_typed_fact_reextraction(item, tool_rows, item_index=index)
            or check_staleness(item, tool_rows, moment, item_index=index)
        )
        if failure is None:
            verified.append(item)
        else:
            failures.append(failure)

    return VerificationResult(verified=verified, failures=failures)


__all__ = [
    "VerificationResult",
    "verify_items",
]
