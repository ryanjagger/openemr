"""Tests for the chat live-eval harness using the deterministic mock LLM."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from evals import run_chat_eval


def _date_token(offset_days: int) -> str:
    return (datetime.now(tz=UTC).date() + timedelta(days=offset_days)).isoformat()


def _medication_fixture() -> dict[str, Any]:
    recent = _date_token(-30)
    return {
        "__id__": "chat_test_medication",
        "label": "test medication fixture",
        "turns": [
            {
                "label": "active meds",
                "user": "What medications is she on?",
                "expectations": {
                    "min_verified_facts": 1,
                    "expected_fact_types_present": ["medication"],
                    "expected_citations": ["med-lisinopril"],
                    "expected_tools_called": ["get_active_medications"],
                    "facts_must_contain": ["lisinopril"],
                },
            }
        ],
        "fhir": {
            "MedicationRequest": {
                "resourceType": "Bundle",
                "entry": [
                    {
                        "resource": {
                            "resourceType": "MedicationRequest",
                            "id": "med-lisinopril",
                            "subject": {
                                "reference": "Patient/eval-patient-fixture",
                            },
                            "status": "active",
                            "medicationCodeableConcept": {
                                "text": "Lisinopril 10 mg oral tablet",
                            },
                            "authoredOn": recent,
                            "meta": {"lastUpdated": f"{recent}T00:00:00+00:00"},
                        },
                    }
                ],
            },
        },
    }


@pytest.mark.asyncio
async def test_mock_chat_eval_runs_tool_loop_and_checks_expectations() -> None:
    llm = run_chat_eval._build_llm("mock", "mock")

    rows = await run_chat_eval._run_fixture(_medication_fixture(), llm, "unit")

    assert len(rows) == 1
    row = rows[0]
    assert row["error"] is None
    assert row["tool_calls"] == ["get_active_medications"]
    assert row["facts_verified"] == 1
    assert all(row["expectations_met"].values()), row["expectations_met"]
    assert row["facts"][0]["citations"][0]["resource_id"] == "med-lisinopril"


@pytest.mark.asyncio
async def test_mock_chat_eval_carries_cached_context_across_turns() -> None:
    fixture = _medication_fixture()
    fixture["__id__"] = "chat_test_multiturn_cache"
    fixture["turns"].append(
        {
            "label": "dose follow-up",
            "user": "What dose of lisinopril is listed?",
            "expectations": {
                "min_verified_facts": 1,
                "expected_citations": ["med-lisinopril"],
                "max_tool_calls": 0,
                "facts_must_contain": ["10 mg"],
            },
        }
    )
    llm = run_chat_eval._build_llm("mock", "mock")

    rows = await run_chat_eval._run_fixture(fixture, llm, "unit")

    assert len(rows) == 2
    first, second = rows
    assert first["tool_calls"] == ["get_active_medications"]
    assert second["tool_calls"] == []
    assert second["cache_rows_before"] == 1
    assert second["cache_rows_after"] == 1
    assert all(second["expectations_met"].values()), second["expectations_met"]


def test_chat_expectation_checker_flags_wrong_tool() -> None:
    result = run_chat_eval._check_chat_expectations(
        narrative="The available chart results do not contain that information.",
        facts=[],
        failures=[],
        tool_calls=["get_allergies"],
        expectations={
            "expected_tools_called": ["get_active_medications"],
            "max_verified_facts": 0,
        },
    )

    assert result == {
        "max_verified_facts": True,
        "expected_tools_called": False,
    }
