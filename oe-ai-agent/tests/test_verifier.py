"""Unit tests for Tier 1 + Tier 2 verifier rules.

The verifier is the safety net: every rule needs both a pass case and
a fail case. These run isolated (no FHIR, no LLM) and are the fastest
signal that the deterministic gate still works.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from oe_ai_agent.schemas.brief import BriefItem, BriefItemType, Citation
from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.verifier import verify_items
from oe_ai_agent.verifier.tier1_structural import (
    check_citations_exist,
    check_patient_binding,
    check_staleness,
    check_type_table_compatibility,
    check_typed_fact_reextraction,
)
from oe_ai_agent.verifier.tier2_schema import check_advisory_denylist, check_citation_floor

PATIENT = "patient-uuid-1"
NOW = datetime(2026, 4, 29, tzinfo=UTC)


def _row(
    resource_type: str,
    resource_id: str,
    *,
    patient_id: str = PATIENT,
    last_updated: datetime = NOW,
    fields: dict | None = None,
    verbatim: str | None = None,
) -> TypedRow:
    return TypedRow(
        resource_type=resource_type,
        resource_id=resource_id,
        patient_id=patient_id,
        last_updated=last_updated,
        fields=fields or {},
        verbatim_excerpt=verbatim,
    )


def _item(
    item_type: BriefItemType,
    text: str,
    citations: list[tuple[str, str]],
) -> BriefItem:
    return BriefItem(
        type=item_type,
        text=text,
        citations=[Citation(resource_type=t, resource_id=i) for t, i in citations],
    )


# ----- Tier 1 -----------------------------------------------------------


def test_citations_exist_pass() -> None:
    rows = [_row("MedicationRequest", "med-1")]
    item = _item(BriefItemType.MED_CURRENT, "On Lisinopril", [("MedicationRequest", "med-1")])
    assert check_citations_exist(item, rows) is None


def test_citations_exist_fail_when_id_fabricated() -> None:
    rows = [_row("MedicationRequest", "med-1")]
    item = _item(
        BriefItemType.MED_CURRENT,
        "On Lisinopril",
        [("MedicationRequest", "fabricated")],
    )
    failure = check_citations_exist(item, rows)
    assert failure is not None
    assert failure.rule == "tier1_citations_exist"


def test_patient_binding_fail_when_other_patient() -> None:
    rows = [_row("MedicationRequest", "med-1", patient_id="other-patient")]
    item = _item(BriefItemType.MED_CURRENT, "On Lisinopril", [("MedicationRequest", "med-1")])
    failure = check_patient_binding(item, rows, expected_patient_uuid=PATIENT)
    assert failure is not None
    assert failure.rule == "tier1_patient_binding"


def test_type_table_compatibility_fail_when_wrong_table() -> None:
    item = _item(BriefItemType.ALLERGY, "Penicillin allergy", [("MedicationRequest", "med-1")])
    failure = check_type_table_compatibility(item)
    assert failure is not None
    assert failure.rule == "tier1_type_table_compatibility"


def test_typed_fact_reextraction_pass_when_number_present() -> None:
    rows = [_row("Observation", "obs-1", verbatim="creatinine 1.8 mg/dL")]
    item = _item(BriefItemType.OVERDUE, "creatinine 1.8 last check", [("Observation", "obs-1")])
    assert check_typed_fact_reextraction(item, rows) is None


def test_typed_fact_reextraction_fail_when_number_drifts() -> None:
    rows = [_row("Observation", "obs-1", verbatim="creatinine 1.8 mg/dL")]
    item = _item(BriefItemType.OVERDUE, "creatinine 8.1 last check", [("Observation", "obs-1")])
    failure = check_typed_fact_reextraction(item, rows)
    assert failure is not None
    assert failure.rule == "tier1_typed_fact_reextraction"


def test_staleness_fail_when_too_old() -> None:
    old = NOW - timedelta(days=400)
    rows = [_row("MedicationRequest", "med-1", last_updated=old)]
    item = _item(BriefItemType.MED_CURRENT, "On Lisinopril", [("MedicationRequest", "med-1")])
    failure = check_staleness(item, rows, now=NOW)
    assert failure is not None
    assert failure.rule == "tier1_staleness"


def test_staleness_skipped_for_no_max_age_types() -> None:
    very_old = NOW - timedelta(days=10_000)
    rows = [_row("AllergyIntolerance", "allergy-1", last_updated=very_old)]
    item = _item(BriefItemType.ALLERGY, "Penicillin", [("AllergyIntolerance", "allergy-1")])
    assert check_staleness(item, rows, now=NOW) is None


# ----- Tier 2 -----------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    ["I recommend statin therapy", "you should follow up", "consider stopping ibuprofen"],
)
def test_advisory_denylist_blocks_advice_phrases(phrase: str) -> None:
    item = _item(BriefItemType.MED_CURRENT, phrase, [("MedicationRequest", "med-1")])
    failure = check_advisory_denylist(item)
    assert failure is not None
    assert failure.rule == "tier2_advisory_denylist"


def test_citation_floor_blocks_zero_citations() -> None:
    item = BriefItem(type=BriefItemType.MED_CURRENT, text="naked claim", citations=[])
    failure = check_citation_floor(item)
    assert failure is not None
    assert failure.rule == "tier2_citation_floor"


def test_disabled_type_dropped_when_freetext_types_off() -> None:
    """T3.10: when AI_AGENT_ENABLE_FREETEXT_TYPES is off, recent_event and
    agenda_item items are dropped with rule=tier2_type_disabled, even if every
    other Tier 1/2 check would pass."""
    rows = [_row("Encounter", "enc-1", verbatim="follow-up visit")]
    items = [
        _item(BriefItemType.RECENT_EVENT, "follow-up visit", [("Encounter", "enc-1")]),
        _item(BriefItemType.MED_CURRENT, "On Lisinopril", [("MedicationRequest", "med-1")]),
    ]
    rows_with_med = [*rows, _row("MedicationRequest", "med-1")]
    structured_only = frozenset(BriefItemType) - {
        BriefItemType.RECENT_EVENT,
        BriefItemType.AGENDA_ITEM,
    }

    result = verify_items(
        items,
        rows_with_med,
        expected_patient_uuid=PATIENT,
        now=NOW,
        allowed_types=structured_only,
    )

    assert {i.text for i in result.verified} == {"On Lisinopril"}
    assert len(result.failures) == 1
    assert result.failures[0].rule == "tier2_type_disabled"
    assert result.failures[0].item_index == 0


# ----- Orchestrator -----------------------------------------------------


def test_orchestrator_drops_only_offending_items() -> None:
    rows = [
        _row("MedicationRequest", "med-1"),
        _row("AllergyIntolerance", "allergy-1"),
    ]
    items = [
        _item(BriefItemType.MED_CURRENT, "On Lisinopril", [("MedicationRequest", "med-1")]),
        _item(BriefItemType.MED_CURRENT, "Bogus", [("Patient", "fabricated")]),
        _item(BriefItemType.ALLERGY, "Penicillin", [("AllergyIntolerance", "allergy-1")]),
    ]
    result = verify_items(items, rows, expected_patient_uuid=PATIENT, now=NOW)
    assert len(result.verified) == 2
    assert {i.text for i in result.verified} == {"On Lisinopril", "Penicillin"}
    assert len(result.failures) == 1
    assert result.failures[0].item_index == 1
