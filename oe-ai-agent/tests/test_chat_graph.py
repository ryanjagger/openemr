"""End-to-end chat graph tests with mocked FHIR + scripted LLM."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.agent.graph_chat import build_chat_graph
from oe_ai_agent.llm.client import LlmChatResult, LlmToolCall
from oe_ai_agent.llm.mock_client import MockLlmClient
from oe_ai_agent.schemas.chat import ChatMessage, ChatRole

PATIENT = "patient-uuid-1"
FHIR_BASE = "http://fhir.test/apis/default/fhir"


def _fhir_routes(mock: respx.MockRouter) -> None:
    mock.get(f"{FHIR_BASE}/Patient/{PATIENT}").mock(
        return_value=httpx.Response(
            200,
            json={
                "resourceType": "Patient",
                "id": PATIENT,
                "name": [{"text": "Karen Liu"}],
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
                            "subject": {"reference": f"Patient/{PATIENT}"},
                            "status": "active",
                            "medicationCodeableConcept": {"text": "Lisinopril 10 mg"},
                            "authoredOn": "2026-01-01",
                            "meta": {"lastUpdated": "2026-04-29T00:00:00+00:00"},
                        }
                    }
                ],
            },
        )
    )
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


def _initial_state(history: list[ChatMessage]) -> ChatState:
    return ChatState(
        patient_uuid=PATIENT,
        fhir_base_url=FHIR_BASE,
        bearer_token="bearer-stub",
        request_id="r-1",
        conversation_id="conv-1",
        history=history,
    )


@pytest.mark.asyncio
async def test_happy_path_emits_narrative_and_verified_facts() -> None:
    envelope = json.dumps(
        {
            "narrative": "She is on lisinopril 10 mg [^1].",
            "facts": [
                {
                    "type": "med_current",
                    "text": "Lisinopril 10 mg, active",
                    "verbatim_excerpts": ["Lisinopril 10 mg"],
                    "citations": [
                        {"resource_type": "MedicationRequest", "resource_id": "med-1"}
                    ],
                    "anchor": 1,
                }
            ],
        }
    )
    llm = MockLlmClient(chat_scripted=LlmChatResult(content=envelope, tool_calls=[]))

    with respx.mock(assert_all_called=False) as mock:
        _fhir_routes(mock)
        graph = build_chat_graph(llm)
        final = await graph.ainvoke(  # type: ignore[attr-defined]
            _initial_state([ChatMessage(role=ChatRole.USER, content="meds?")])
        )

    assert final["parsed_narrative"] == "She is on lisinopril 10 mg [^1]."
    assert len(final["verified_facts"]) == 1
    assert final["verified_facts"][0].citations[0].resource_id == "med-1"
    assert final["verification_failures"] == []


@pytest.mark.asyncio
async def test_unground_number_in_narrative_is_replaced_with_fallback() -> None:
    envelope = json.dumps(
        {
            "narrative": "She is on lisinopril 20 mg.",  # 20 is not in facts
            "facts": [
                {
                    "type": "med_current",
                    "text": "Lisinopril 10 mg, active",
                    "verbatim_excerpts": ["Lisinopril 10 mg"],
                    "citations": [
                        {"resource_type": "MedicationRequest", "resource_id": "med-1"}
                    ],
                }
            ],
        }
    )
    llm = MockLlmClient(chat_scripted=LlmChatResult(content=envelope, tool_calls=[]))

    with respx.mock(assert_all_called=False) as mock:
        _fhir_routes(mock)
        graph = build_chat_graph(llm)
        final = await graph.ainvoke(  # type: ignore[attr-defined]
            _initial_state([ChatMessage(role=ChatRole.USER, content="meds?")])
        )

    # Verified fact survives — narrative is the only thing replaced.
    assert len(final["verified_facts"]) == 1
    assert "fact cards below are verified" in final["parsed_narrative"]
    assert any(
        f.rule == "tier1_narrative_grounding" for f in final["verification_failures"]
    )


@pytest.mark.asyncio
async def test_tool_call_drives_lab_trend_and_returns_grounded_envelope() -> None:
    """When the model emits a tool_call, the loop runs it and the second
    LLM turn produces the final envelope."""
    a1c_bundle = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Observation",
                    "id": "obs-a1c",
                    "subject": {"reference": f"Patient/{PATIENT}"},
                    "code": {"text": "Hemoglobin A1c"},
                    "valueQuantity": {"value": 7.2, "unit": "%"},
                    "effectiveDateTime": "2026-01-15",
                    "meta": {"lastUpdated": "2026-01-15T00:00:00+00:00"},
                }
            }
        ],
    }

    call_state = {"count": 0}

    def script(messages: list[dict[str, Any]]) -> LlmChatResult:
        call_state["count"] += 1
        if call_state["count"] == 1:
            return LlmChatResult(
                content=None,
                tool_calls=[
                    LlmToolCall(
                        tool_call_id="t1",
                        name="get_lab_trend",
                        arguments={"code_or_text": "4548-4"},
                    )
                ],
            )
        # Second pass: tool result is in the message list, emit envelope.
        return LlmChatResult(
            content=json.dumps(
                {
                    "narrative": "Most recent A1c was 7.2 [^1].",
                    "facts": [
                        {
                            "type": "overdue",
                            "text": "A1c 7.2 % on 2026-01-15",
                            "verbatim_excerpts": ["7.2", "2026-01-15"],
                            "citations": [
                                {
                                    "resource_type": "Observation",
                                    "resource_id": "obs-a1c",
                                }
                            ],
                            "anchor": 1,
                        }
                    ],
                }
            ),
            tool_calls=[],
        )

    llm = MockLlmClient(chat_scripted=script)

    with respx.mock(assert_all_called=False) as mock:
        _fhir_routes(mock)
        # Override the empty Observation route with a code-keyed response.
        mock.get(f"{FHIR_BASE}/Observation").mock(
            return_value=httpx.Response(200, json=a1c_bundle)
        )
        graph = build_chat_graph(llm)
        final = await graph.ainvoke(  # type: ignore[attr-defined]
            _initial_state([ChatMessage(role=ChatRole.USER, content="A1c trend?")])
        )

    assert call_state["count"] == 2
    assert any(
        f.citations[0].resource_id == "obs-a1c" for f in final["verified_facts"]
    )
    assert "7.2" in final["parsed_narrative"]
