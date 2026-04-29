"""Tier 2 verifier rules — schema-level constraints not enforceable by Pydantic alone.

The closed ``BriefItemType`` enum is enforced at parse time by Pydantic,
so the model literally cannot emit a free-form claim type through the
structured-output path. This module covers three remaining rules:
the advisory-phrase denylist, the citation-count floor, and the
runtime-disabled-type check (T3.10 — gates free-text-derived types
behind ``AI_AGENT_ENABLE_FREETEXT_TYPES``).
"""

from __future__ import annotations

from oe_ai_agent.schemas.brief import BriefItem, BriefItemType, VerificationFailure
from oe_ai_agent.verifier.constraints import ADVISORY_DENYLIST


def check_advisory_denylist(
    item: BriefItem,
    item_index: int | None = None,
) -> VerificationFailure | None:
    match = ADVISORY_DENYLIST.search(item.text)
    if match is None:
        return None
    return VerificationFailure(
        rule="tier2_advisory_denylist",
        detail=f"text contains denied advisory phrase {match.group(0)!r}",
        item_index=item_index,
    )


def check_citation_floor(
    item: BriefItem,
    item_index: int | None = None,
) -> VerificationFailure | None:
    if not item.citations:
        return VerificationFailure(
            rule="tier2_citation_floor",
            detail="item has no citations",
            item_index=item_index,
        )
    return None


def check_disabled_type(
    item: BriefItem,
    allowed_types: frozenset[BriefItemType],
    item_index: int | None = None,
) -> VerificationFailure | None:
    if item.type in allowed_types:
        return None
    return VerificationFailure(
        rule="tier2_type_disabled",
        detail=f"type {item.type.value!r} is disabled in this deployment",
        item_index=item_index,
    )
