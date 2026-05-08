"""Tests for get_lab_trend tool — query construction + TypedRow output."""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from oe_ai_agent.tools import FhirClient, get_lab_trend

PATIENT = "patient-uuid-1"
FHIR_BASE = "http://fhir.test/apis/default/fhir"


def _bundle(*entries: dict[str, object]) -> dict[str, object]:
    return {"resourceType": "Bundle", "entry": [{"resource": e} for e in entries]}


@pytest.mark.asyncio
async def test_loinc_code_is_passed_with_system() -> None:
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json=_bundle())

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{FHIR_BASE}/Observation").mock(side_effect=_capture)
        async with FhirClient(base_url=FHIR_BASE, bearer_token="t") as client:
            await get_lab_trend(client, PATIENT, code_or_text="4548-4")

    assert captured.get("code") == "http://loinc.org|4548-4"
    assert captured.get("category") == "laboratory"
    assert captured.get("patient") == PATIENT


@pytest.mark.asyncio
async def test_freetext_fetches_wide_window_and_filters_client_side() -> None:
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            json=_bundle(
                {
                    "resourceType": "Observation",
                    "id": "obs-a1c",
                    "subject": {"reference": f"Patient/{PATIENT}"},
                    "code": {"text": "Hemoglobin A1c"},
                    "valueQuantity": {"value": 7.2, "unit": "%"},
                    "effectiveDateTime": "2026-01-15",
                    "meta": {"lastUpdated": "2026-01-15T00:00:00+00:00"},
                },
                {
                    "resourceType": "Observation",
                    "id": "obs-creatinine",
                    "subject": {"reference": f"Patient/{PATIENT}"},
                    "code": {"text": "Creatinine"},
                    "valueQuantity": {"value": 1.1, "unit": "mg/dL"},
                    "effectiveDateTime": "2026-01-15",
                    "meta": {"lastUpdated": "2026-01-15T00:00:00+00:00"},
                },
            ),
        )

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{FHIR_BASE}/Observation").mock(side_effect=_capture)
        async with FhirClient(base_url=FHIR_BASE, bearer_token="t") as client:
            rows = await get_lab_trend(client, PATIENT, code_or_text="hemoglobin a1c")

    assert "code" not in captured
    assert "code:text" not in captured
    assert captured.get("_count") == "200"
    assert [row.resource_id for row in rows] == ["obs-a1c"]


@pytest.mark.asyncio
async def test_since_filter_is_prefixed_with_ge() -> None:
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json=_bundle())

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{FHIR_BASE}/Observation").mock(side_effect=_capture)
        async with FhirClient(base_url=FHIR_BASE, bearer_token="t") as client:
            await get_lab_trend(
                client,
                PATIENT,
                code_or_text="4548-4",
                since=date(2025, 1, 1),
            )

    assert captured.get("date") == "ge2025-01-01"


@pytest.mark.asyncio
async def test_returns_typed_rows_for_each_observation() -> None:
    body = _bundle(
        {
            "resourceType": "Observation",
            "id": "obs-1",
            "subject": {"reference": f"Patient/{PATIENT}"},
            "code": {"text": "Hemoglobin A1c"},
            "valueQuantity": {"value": 7.2, "unit": "%"},
            "effectiveDateTime": "2026-01-15",
            "meta": {"lastUpdated": "2026-01-15T00:00:00+00:00"},
        },
        {
            "resourceType": "Observation",
            "id": "obs-2",
            "subject": {"reference": f"Patient/{PATIENT}"},
            "code": {"text": "Hemoglobin A1c"},
            "valueQuantity": {"value": 7.8, "unit": "%"},
            "effectiveDateTime": "2025-09-15",
            "meta": {"lastUpdated": "2025-09-15T00:00:00+00:00"},
        },
    )
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{FHIR_BASE}/Observation").mock(
            return_value=httpx.Response(200, json=body)
        )
        async with FhirClient(base_url=FHIR_BASE, bearer_token="t") as client:
            rows = await get_lab_trend(client, PATIENT, code_or_text="4548-4")

    assert [r.resource_id for r in rows] == ["obs-1", "obs-2"]
    assert all(r.patient_id == PATIENT for r in rows)
    assert rows[0].fields.get("valueQuantity") == {"value": 7.2, "unit": "%"}
