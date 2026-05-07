"""Tests for the chat live-eval harness using the deterministic mock LLM."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
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


@pytest.mark.asyncio
async def test_all_chat_fixtures_pass_deterministic_mock_gate() -> None:
    fixtures = run_chat_eval.load_fixtures(run_chat_eval.DEFAULT_CHAT_FIXTURES_DIR)
    llm = run_chat_eval._build_llm("mock", "mock")
    failures: list[dict[str, Any]] = []

    for fixture in fixtures:
        rows = await run_chat_eval._run_fixture(fixture, llm, "unit")
        for row in rows:
            if row["known_limitation"] or not run_chat_eval._row_failed(row):
                continue
            failures.append(
                {
                    "fixture_id": row["fixture_id"],
                    "turn_index": row["turn_index"],
                    "error": row["error"],
                    "parse_error": row["parse_error"],
                    "expectations_met": row["expectations_met"],
                    "tool_calls": row["tool_calls"],
                    "narrative": row["narrative"],
                }
            )

    assert failures == []


def test_fail_on_expectations_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_run_fixture(
        fixture: dict[str, Any],
        llm: run_chat_eval.LlmClient,
        label: str,
    ) -> list[dict[str, Any]]:
        del fixture, llm, label
        return [
            {
                "fixture_id": "chat_test_failed_expectation",
                "turn_index": 1,
                "known_limitation": False,
                "error": None,
                "parse_error": None,
                "expectations_met": {"expected_tools_called": False},
                "facts_verified": 0,
                "tool_call_count": 0,
                "duration_ms": 1,
            }
        ]

    monkeypatch.setattr(
        run_chat_eval,
        "load_fixtures",
        lambda fixtures_dir, only=None: [{"__id__": "chat_test_failed_expectation"}],
    )
    monkeypatch.setattr(run_chat_eval, "_run_fixture", fake_run_fixture)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_chat_eval.py",
            "--label",
            "unit",
            "--provider",
            "mock",
            "--output",
            str(tmp_path / "chat-eval.jsonl"),
            "--fail-on-expectations",
        ],
    )

    assert run_chat_eval.main() == 1
