"""Unit tests for the narrative grounding rule."""

from __future__ import annotations

from oe_ai_agent.schemas.brief import BriefItem, BriefItemType, Citation
from oe_ai_agent.verifier.narrative import check_narrative_grounding


def _fact(text: str, excerpts: list[str], anchor: int | None = None) -> BriefItem:
    return BriefItem(
        type=BriefItemType.MED_CURRENT,
        text=text,
        verbatim_excerpts=excerpts,
        citations=[Citation(resource_type="MedicationRequest", resource_id="med-1")],
        anchor=anchor,
    )


def test_pass_when_narrative_has_no_numbers() -> None:
    facts = [_fact("On lisinopril", ["Lisinopril 10 mg"])]
    assert (
        check_narrative_grounding("She is on lisinopril for hypertension.", facts)
        is None
    )


def test_pass_when_numbers_appear_in_excerpts() -> None:
    facts = [_fact("On lisinopril 10 mg", ["Lisinopril 10 mg"], anchor=1)]
    failure = check_narrative_grounding(
        "She's been on lisinopril 10 mg [^1] since admission.",
        facts,
    )
    assert failure is None


def test_pass_when_numbers_appear_in_verified_fact_text() -> None:
    facts = [_fact("Most recent A1c was 7.0", [], anchor=1)]
    failure = check_narrative_grounding(
        "Most recent A1c was 7 [^1].",
        facts,
    )
    assert failure is None


def test_fail_when_narrative_invents_a_number() -> None:
    facts = [_fact("On lisinopril 10 mg", ["Lisinopril 10 mg"])]
    failure = check_narrative_grounding(
        "She's been on lisinopril 20 mg since admission.",
        facts,
    )
    assert failure is not None
    assert failure.rule == "tier1_narrative_grounding"
    assert "20" in failure.detail


def test_anchor_pills_do_not_trigger() -> None:
    facts = [_fact("Note", ["clinical note"], anchor=2)]
    assert (
        check_narrative_grounding("See note [^2] for context.", facts) is None
    )


def test_fail_when_date_drifts() -> None:
    facts = [_fact("Started Mar 2026", ["Started 2026-03-01"])]
    failure = check_narrative_grounding(
        "Started on 2025-03-01 per the chart.",
        facts,
    )
    assert failure is not None
    assert failure.rule == "tier1_narrative_grounding"


def test_advisory_phrase_in_narrative_blocks() -> None:
    facts = [_fact("On lisinopril", ["Lisinopril"])]
    failure = check_narrative_grounding(
        "I recommend stopping the lisinopril.",
        facts,
    )
    assert failure is not None
    assert failure.rule == "tier2_advisory_denylist"


def test_empty_narrative_passes() -> None:
    assert check_narrative_grounding("   ", []) is None
