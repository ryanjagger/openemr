"""Tests for model-callable chat tool registry and new patient context tools."""

from __future__ import annotations

import httpx
import pytest
import respx

from oe_ai_agent.llm.client import LlmToolCall
from oe_ai_agent.tools import FhirClient
from oe_ai_agent.tools.chat_registry import chat_tools_schema, execute_chat_tool

PATIENT = "patient-uuid-1"
FHIR_BASE = "http://fhir.test/apis/default/fhir"


def _bundle(*entries: dict[str, object]) -> dict[str, object]:
    return {"resourceType": "Bundle", "entry": [{"resource": e} for e in entries]}


@pytest.mark.asyncio
async def test_registry_exposes_new_patient_visibility_tools() -> None:
    names: set[str] = set()
    for tool in chat_tools_schema():
        function = tool.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str):
                names.add(name)

    assert {
        "get_lab_trend",
        "get_observations",
        "get_medication_history",
        "get_orders",
        "get_procedures",
        "get_immunizations",
    }.issubset(names)


@pytest.mark.asyncio
async def test_get_observations_builds_category_text_and_date_query() -> None:
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            json=_bundle(
                {
                    "resourceType": "Observation",
                    "id": "obs-bp",
                    "subject": {"reference": f"Patient/{PATIENT}"},
                    "category": [{"text": "Vital Signs"}],
                    "code": {"text": "Blood pressure"},
                    "valueString": "120/80",
                    "effectiveDateTime": "2026-04-01",
                    "meta": {"lastUpdated": "2026-04-01T00:00:00+00:00"},
                }
            ),
        )

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{FHIR_BASE}/Observation").mock(side_effect=_capture)
        async with FhirClient(base_url=FHIR_BASE, bearer_token="t") as client:
            rows, error, _payload = await execute_chat_tool(
                LlmToolCall(
                    tool_call_id="t1",
                    name="get_observations",
                    arguments={
                        "category": "vital-signs",
                        "code_or_text": "blood pressure",
                        "since": "2026-01-01",
                    },
                ),
                client,
                PATIENT,
            )

    assert error is None
    assert captured.get("patient") == PATIENT
    assert captured.get("category") == "vital-signs"
    assert captured.get("code:text") == "blood pressure"
    assert captured.get("date") == "ge2026-01-01"
    assert rows[0].resource_id == "obs-bp"
    assert rows[0].fields["valueString"] == "120/80"


@pytest.mark.asyncio
async def test_get_medication_history_allows_status_and_since_filters() -> None:
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            json=_bundle(
                {
                    "resourceType": "MedicationRequest",
                    "id": "med-old",
                    "subject": {"reference": f"Patient/{PATIENT}"},
                    "status": "stopped",
                    "medicationCodeableConcept": {"text": "Metformin 500 mg"},
                    "authoredOn": "2026-02-01",
                    "meta": {"lastUpdated": "2026-02-01T00:00:00+00:00"},
                }
            ),
        )

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{FHIR_BASE}/MedicationRequest").mock(side_effect=_capture)
        async with FhirClient(base_url=FHIR_BASE, bearer_token="t") as client:
            rows, error, _payload = await execute_chat_tool(
                LlmToolCall(
                    tool_call_id="t1",
                    name="get_medication_history",
                    arguments={"status": "stopped", "since": "2026-01-01"},
                ),
                client,
                PATIENT,
            )

    assert error is None
    assert captured.get("status") == "stopped"
    assert captured.get("authoredon") == "ge2026-01-01"
    assert rows[0].fields["status"] == "stopped"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "resource_type", "text_param"),
    [
        ("get_orders", "ServiceRequest", "code:text"),
        ("get_procedures", "Procedure", "code:text"),
        ("get_immunizations", "Immunization", "vaccine-code:text"),
    ],
)
async def test_new_resource_tools_search_expected_fhir_resources(
    tool_name: str,
    resource_type: str,
    text_param: str,
) -> None:
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            json=_bundle(
                {
                    "resourceType": resource_type,
                    "id": f"{resource_type.lower()}-1",
                    "subject": {"reference": f"Patient/{PATIENT}"},
                    "status": "completed",
                    "code": {"text": "Example"},
                    "meta": {"lastUpdated": "2026-03-01T00:00:00+00:00"},
                }
            ),
        )

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{FHIR_BASE}/{resource_type}").mock(side_effect=_capture)
        async with FhirClient(base_url=FHIR_BASE, bearer_token="t") as client:
            rows, error, _payload = await execute_chat_tool(
                LlmToolCall(
                    tool_call_id="t1",
                    name=tool_name,
                    arguments={"status": "completed", "code_or_text": "example"},
                ),
                client,
                PATIENT,
            )

    assert error is None
    assert captured.get("patient") == PATIENT
    assert captured.get("status") == "completed"
    assert captured.get(text_param) == "example"
    assert rows[0].resource_type == resource_type
