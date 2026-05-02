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
        "get_demographics",
        "get_active_problems",
        "get_active_medications",
        "get_allergies",
        "get_recent_encounters",
        "get_recent_notes",
        "get_lab_trend",
        "get_observations",
        "get_medication_history",
        "get_orders",
        "get_procedures",
        "get_immunizations",
        "get_appointments",
        "get_care_plan_goals",
    }.issubset(names)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "resource_type", "expected_params"),
    [
        (
            "get_active_problems",
            "Condition",
            {"patient": PATIENT, "category": "problem-list-item"},
        ),
        (
            "get_active_medications",
            "MedicationRequest",
            {"patient": PATIENT, "status": "active"},
        ),
        ("get_allergies", "AllergyIntolerance", {"patient": PATIENT}),
        (
            "get_recent_encounters",
            "Encounter",
            {"patient": PATIENT, "_count": "3", "_sort": "-date"},
        ),
        (
            "get_recent_notes",
            "DocumentReference",
            {"patient": PATIENT, "_count": "3", "_sort": "-date"},
        ),
    ],
)
async def test_basic_chart_tools_search_expected_fhir_resources(
    tool_name: str,
    resource_type: str,
    expected_params: dict[str, str],
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
                    "status": "active",
                    "code": {"text": "Example"},
                    "meta": {"lastUpdated": "2026-03-01T00:00:00+00:00"},
                }
            ),
        )

    arguments = {"limit": 3} if tool_name.startswith("get_recent_") else {}
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{FHIR_BASE}/{resource_type}").mock(side_effect=_capture)
        async with FhirClient(base_url=FHIR_BASE, bearer_token="t") as client:
            rows, error, _payload = await execute_chat_tool(
                LlmToolCall(
                    tool_call_id="t1",
                    name=tool_name,
                    arguments=arguments,
                ),
                client,
                PATIENT,
            )

    assert error is None
    assert captured == expected_params
    assert rows[0].resource_type == resource_type


@pytest.mark.asyncio
async def test_get_demographics_reads_patient_resource() -> None:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(f"{FHIR_BASE}/Patient/{PATIENT}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "resourceType": "Patient",
                    "id": PATIENT,
                    "name": [{"text": "Karen Liu"}],
                    "birthDate": "1980-01-01",
                    "gender": "female",
                    "meta": {"lastUpdated": "2026-03-01T00:00:00+00:00"},
                },
            )
        )
        async with FhirClient(base_url=FHIR_BASE, bearer_token="t") as client:
            rows, error, _payload = await execute_chat_tool(
                LlmToolCall(
                    tool_call_id="t1",
                    name="get_demographics",
                    arguments={},
                ),
                client,
                PATIENT,
            )

    assert error is None
    assert route.called
    assert rows[0].resource_type == "Patient"
    assert rows[0].fields["birthDate"] == "1980-01-01"


@pytest.mark.asyncio
async def test_no_arg_tools_reject_unexpected_arguments() -> None:
    async with FhirClient(base_url=FHIR_BASE, bearer_token="t") as client:
        rows, error, payload = await execute_chat_tool(
            LlmToolCall(
                tool_call_id="t1",
                name="get_demographics",
                arguments={"unexpected": "value"},
            ),
            client,
            PATIENT,
        )

    assert rows == []
    assert error is not None
    assert error.tool_name == "get_demographics"
    assert error.message == "this tool does not accept arguments"
    assert payload == {"error": "this tool does not accept arguments"}


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
async def test_get_appointments_searches_patient_with_limit() -> None:
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            json=_bundle(
                {
                    "resourceType": "Appointment",
                    "id": "appt-1",
                    "status": "booked",
                    "start": "2026-05-03T14:00:00+00:00",
                    "participant": [{"actor": {"reference": f"Patient/{PATIENT}"}}],
                    "meta": {"lastUpdated": "2026-04-01T00:00:00+00:00"},
                }
            ),
        )

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{FHIR_BASE}/Appointment").mock(side_effect=_capture)
        async with FhirClient(base_url=FHIR_BASE, bearer_token="t") as client:
            rows, error, _payload = await execute_chat_tool(
                LlmToolCall(
                    tool_call_id="t1",
                    name="get_appointments",
                    arguments={"limit": 7, "since": "2026-01-01"},
                ),
                client,
                PATIENT,
            )

    assert error is None
    assert captured == {
        "patient": PATIENT,
        "_count": "7",
        "_sort": "-date",
        "date": "ge2026-01-01",
    }
    assert rows[0].resource_type == "Appointment"
    assert rows[0].patient_id == PATIENT
    assert rows[0].fields["status"] == "booked"


@pytest.mark.asyncio
async def test_get_care_plan_goals_searches_both_resources() -> None:
    captured: dict[str, dict[str, str]] = {}

    def _capture(name: str) -> object:
        def capture(request: httpx.Request) -> httpx.Response:
            captured[name] = dict(request.url.params)
            return httpx.Response(
                200,
                json=_bundle(
                    {
                        "resourceType": name,
                        "id": f"{name.lower()}-1",
                        "subject": {"reference": f"Patient/{PATIENT}"},
                        "status": "active",
                        "lifecycleStatus": "active",
                        "description": {"text": "Increase activity"},
                        "meta": {"lastUpdated": "2026-04-01T00:00:00+00:00"},
                    }
                ),
            )

        return capture

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{FHIR_BASE}/CarePlan").mock(side_effect=_capture("CarePlan"))
        mock.get(f"{FHIR_BASE}/Goal").mock(side_effect=_capture("Goal"))
        async with FhirClient(base_url=FHIR_BASE, bearer_token="t") as client:
            rows, error, _payload = await execute_chat_tool(
                LlmToolCall(
                    tool_call_id="t1",
                    name="get_care_plan_goals",
                    arguments={
                        "category": "assess-plan",
                        "since": "2026-01-01",
                        "limit": 2,
                    },
                ),
                client,
                PATIENT,
            )

    assert error is None
    assert captured["CarePlan"] == {
        "patient": PATIENT,
        "_count": "2",
        "category": "assess-plan",
        "_lastUpdated": "ge2026-01-01",
    }
    assert captured["Goal"] == {
        "patient": PATIENT,
        "_count": "2",
        "_lastUpdated": "ge2026-01-01",
    }
    assert {row.resource_type for row in rows} == {"CarePlan", "Goal"}


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


@pytest.mark.asyncio
async def test_get_active_problems_drops_inactive_clinical_status() -> None:
    def _capture(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_bundle(
                {
                    "resourceType": "Condition",
                    "id": "active-1",
                    "subject": {"reference": f"Patient/{PATIENT}"},
                    "clinicalStatus": {
                        "coding": [
                            {
                                "system": (
                                    "http://terminology.hl7.org/CodeSystem/condition-clinical"
                                ),
                                "code": "active",
                            }
                        ]
                    },
                    "code": {"text": "Chronic pain"},
                    "meta": {"lastUpdated": "2026-03-01T00:00:00+00:00"},
                },
                {
                    "resourceType": "Condition",
                    "id": "inactive-1",
                    "subject": {"reference": f"Patient/{PATIENT}"},
                    "clinicalStatus": {
                        "coding": [
                            {
                                "system": (
                                    "http://terminology.hl7.org/CodeSystem/condition-clinical"
                                ),
                                "code": "resolved",
                            }
                        ]
                    },
                    "code": {"text": "Stress"},
                    "meta": {"lastUpdated": "2026-03-01T00:00:00+00:00"},
                },
            ),
        )

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{FHIR_BASE}/Condition").mock(side_effect=_capture)
        async with FhirClient(base_url=FHIR_BASE, bearer_token="t") as client:
            rows, error, _payload = await execute_chat_tool(
                LlmToolCall(
                    tool_call_id="t1",
                    name="get_active_problems",
                    arguments={},
                ),
                client,
                PATIENT,
            )

    assert error is None
    assert [row.resource_id for row in rows] == ["active-1"]
