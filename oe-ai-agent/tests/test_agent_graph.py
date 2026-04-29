"""End-to-end test for the LangGraph build with mocked FHIR + scripted LLM.

No network. Confirms the graph wires fetch_context → llm_call →
parse_output → verify and that the verifier drops items with bad
citations even when the LLM produced them.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from oe_ai_agent.agent.graph import build_graph
from oe_ai_agent.agent.state import AgentState
from oe_ai_agent.llm.mock_client import MockLlmClient

PATIENT_UUID = "patient-uuid-1"
FHIR_BASE = "http://fhir.test/apis/default/fhir"


def _fhir_routes(mock: respx.MockRouter) -> None:
    """Register minimal FHIR responses so fetch_context returns one MedicationRequest."""
    mock.get(f"{FHIR_BASE}/Patient/{PATIENT_UUID}").mock(
        return_value=httpx.Response(
            200,
            json={
                "resourceType": "Patient",
                "id": PATIENT_UUID,
                "name": [{"text": "Phil Belford"}],
                "meta": {"lastUpdated": "2026-04-29T00:00:00+00:00"},
            },
        )
    )
    mock.get(f"{FHIR_BASE}/MedicationRequest").mock(
        return_value=httpx.Response(
            200,
            json={
                "resourceType": "Bundle",
                "entry": [
                    {
                        "resource": {
                            "resourceType": "MedicationRequest",
                            "id": "med-1",
                            "subject": {"reference": f"Patient/{PATIENT_UUID}"},
                            "status": "active",
                            "medicationCodeableConcept": {"text": "Lisinopril 10mg"},
                            "authoredOn": "2026-01-01",
                            "meta": {"lastUpdated": "2026-04-29T00:00:00+00:00"},
                        },
                    },
                ],
            },
        )
    )
    # Empty bundles for the rest so fetch_context tolerates them.
    for path in (
        "Condition",
        "AllergyIntolerance",
        "Encounter",
        "Observation",
        "DocumentReference",
    ):
        mock.get(f"{FHIR_BASE}/{path}").mock(
            return_value=httpx.Response(200, json={"resourceType": "Bundle", "entry": []}),
        )


@pytest.mark.asyncio
async def test_graph_emits_verified_item_for_real_med_request() -> None:
    with respx.mock(assert_all_called=False) as mock:
        _fhir_routes(mock)
        graph = build_graph(MockLlmClient.synthesizing())
        final = await graph.ainvoke(  # type: ignore[attr-defined]
            AgentState(
                patient_uuid=PATIENT_UUID,
                fhir_base_url=FHIR_BASE,
                bearer_token="bearer-stub",
                request_id="r-1",
            )
        )

    assert final["verified_items"], "expected at least one verified item"
    medication_items = [i for i in final["verified_items"] if i.type.value == "med_current"]
    assert medication_items, "expected the MedicationRequest to surface as med_current"
    assert any(c.resource_id == "med-1" for c in medication_items[0].citations)
    assert final["verification_failures"] == []


@pytest.mark.asyncio
async def test_graph_drops_items_with_fabricated_citations() -> None:
    """A scripted LLM that cites a non-existent id must produce zero verified items."""
    fake_response = json.dumps(
        {
            "items": [
                {
                    "type": "med_current",
                    "text": "On a medication that does not exist",
                    "verbatim_excerpts": [],
                    "citations": [
                        {"resource_type": "MedicationRequest", "resource_id": "fabricated"},
                    ],
                },
            ],
        }
    )
    with respx.mock(assert_all_called=False) as mock:
        _fhir_routes(mock)
        graph = build_graph(MockLlmClient(scripted=fake_response))
        final = await graph.ainvoke(  # type: ignore[attr-defined]
            AgentState(
                patient_uuid=PATIENT_UUID,
                fhir_base_url=FHIR_BASE,
                bearer_token="bearer-stub",
                request_id="r-2",
            )
        )

    assert final["verified_items"] == []
    assert any(
        f.rule == "tier1_citations_exist" for f in final["verification_failures"]
    )
