"""Narrative grounding rule for chat turns.

The chat surface returns a JSON envelope with two fields: a free-prose
``narrative`` shown to the physician, and a typed ``facts: BriefItem[]``
list that goes through the existing Tier 1 + Tier 2 chain. Free prose is
where paraphrase drift hides — "amoxicillin 250 mg" when the chart says
500, "since March 2025" when the note says 2024. This rule re-uses the
machinery from ``check_typed_fact_reextraction`` over the narrative
string and the union of fact-card excerpts.

What's checked:
- Every number in the narrative must appear verbatim in some fact's
  ``verbatim_excerpts`` blob (or be inside a footnote anchor like ``[^2]``).
- Every ISO date in the narrative must appear in that same blob.
- The narrative as a whole runs the advisory denylist.

What's not checked: drug or condition names. Those would need a clinical
ontology to do safely; the closed ``BriefItemType`` enum + the requirement
that every cited resource id exist already block fabricated terms from
landing in fact cards, and the prompt instructs the model to keep clinical
nouns inside fact cards rather than freeform prose.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from oe_ai_agent.schemas.brief import VerificationFailure
from oe_ai_agent.verifier.constraints import ADVISORY_DENYLIST
from oe_ai_agent.verifier.tier1_structural import (
    _DATE_RE,
    _NUMBER_RE,
    _number_equivalent,
    _numbers_in,
)

# Footnote anchors like [^1] reference fact cards, not numeric facts. Strip
# them before scanning for numbers so the anchor index doesn't trigger the
# rule. Also accept a bare [1] form in case the model formats it that way.
_ANCHOR_RE = re.compile(r"\[\^?\d+\]")


class FactExcerptSource(Protocol):
    @property
    def text(self) -> str: ...

    @property
    def verbatim_excerpts(self) -> list[str]: ...


def check_narrative_grounding(
    narrative: str,
    facts: Sequence[FactExcerptSource],
) -> VerificationFailure | None:
    """Return a VerificationFailure if narrative drifts from the fact set."""
    if not narrative.strip():
        return None

    advisory_match = ADVISORY_DENYLIST.search(narrative)
    if advisory_match is not None:
        return VerificationFailure(
            rule="tier2_advisory_denylist",
            detail=(
                f"narrative contains denied advisory phrase "
                f"{advisory_match.group(0)!r}"
            ),
        )

    cleaned = _ANCHOR_RE.sub(" ", narrative)
    grounded_blob = " ".join(
        segment
        for fact in facts
        for segment in [fact.text, *fact.verbatim_excerpts]
        if segment
    )
    grounded_numbers = _numbers_in(grounded_blob)

    for date_match in _DATE_RE.findall(cleaned):
        if date_match not in grounded_blob:
            return VerificationFailure(
                rule="tier1_narrative_grounding",
                detail=f"date {date_match!r} not grounded in any fact excerpt",
            )

    for number_match in _NUMBER_RE.findall(cleaned):
        if number_match not in grounded_blob and not _number_equivalent(
            number_match,
            grounded_numbers,
        ):
            return VerificationFailure(
                rule="tier1_narrative_grounding",
                detail=f"number {number_match!r} not grounded in any verified fact",
            )

    return None
