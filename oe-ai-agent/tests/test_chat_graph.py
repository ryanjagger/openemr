"""End-to-end chat graph tests with mocked FHIR + scripted LLM."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.agent.graph_chat import build_chat_graph
from oe_ai_agent.llm.client import LlmChatResult, LlmToolCall, LlmUsage
from oe_ai_agent.llm.mock_client import MockLlmClient
from oe_ai_agent.observability import use_trace
from oe_ai_agent.schemas.chat import ChatMessage, ChatRole
from oe_ai_agent.schemas.tool_results import TypedRow

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


def _medication_context() -> list[TypedRow]:
    return [
        TypedRow(
            resource_type="MedicationRequest",
            resource_id="med-1",
            patient_id=PATIENT,
            last_updated=datetime(2026, 4, 29, tzinfo=UTC),
            fields={
                "status": "active",
                "medicationCodeableConcept": {"text": "Lisinopril 10 mg"},
            },
        )
    ]


def _initial_state(
    history: list[ChatMessage],
    cached_context: list[TypedRow] | None = None,
) -> ChatState:
    return ChatState(
        patient_uuid=PATIENT,
        fhir_base_url=FHIR_BASE,
        bearer_token="bearer-stub",
        request_id="r-1",
        conversation_id="conv-1",
        history=history,
        cached_context=cached_context or [],
    )


@pytest.mark.asyncio
async def test_happy_path_emits_narrative_and_verified_facts() -> None:
    envelope = json.dumps(
        {
            "narrative": "She is on lisinopril 10 mg [^1].",
            "facts": [
                {
                    "type": "medication",
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
            _initial_state(
                [ChatMessage(role=ChatRole.USER, content="meds?")],
                cached_context=_medication_context(),
            )
        )

    assert final["parsed_narrative"] == "She is on lisinopril 10 mg [^1]."
    assert len(final["verified_facts"]) == 1
    assert final["verified_facts"][0].citations[0].resource_id == "med-1"
    assert final["verification_failures"] == []


@pytest.mark.asyncio
async def test_happy_path_records_step_trace_with_usage() -> None:
    """The trace collector should fill in step records and usage when active."""
    envelope = json.dumps(
        {
            "narrative": "She is on lisinopril 10 mg [^1].",
            "facts": [
                {
                    "type": "medication",
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
    llm = MockLlmClient(
        chat_scripted=LlmChatResult(
            content=envelope,
            tool_calls=[],
            usage=LlmUsage(
                prompt_tokens=42, completion_tokens=8, total_tokens=50, latency_ms=12
            ),
        )
    )

    with respx.mock(assert_all_called=False) as mock:
        _fhir_routes(mock)
        graph = build_chat_graph(llm)
        async with use_trace() as trace:
            await graph.ainvoke(  # type: ignore[attr-defined]
                _initial_state(
                    [ChatMessage(role=ChatRole.USER, content="meds?")],
                    cached_context=_medication_context(),
                )
            )

    steps = trace.to_list()
    step_names = [r["name"] for r in steps]
    # to_list() sorts by start time so the admin timeline reads naturally:
    # ensure_chat_context begins first, verify_chat begins last.
    assert step_names[0] == "ensure_chat_context"
    assert step_names[-1] == "verify_chat"
    assert "llm_turn" in step_names
    assert "parse_envelope" in step_names
    assert not any(name.startswith("tool.get_") for name in step_names)
    assert steps[0]["attrs"]["eager_prefetch"] is False

    summary = trace.usage_summary()
    assert summary["prompt_tokens"] == 42
    assert summary["completion_tokens"] == 8
    assert summary["total_tokens"] == 50


@pytest.mark.asyncio
async def test_unground_number_in_narrative_is_replaced_with_fallback() -> None:
    envelope = json.dumps(
        {
            "narrative": "She is on lisinopril 20 mg.",  # 20 is not in facts
            "facts": [
                {
                    "type": "medication",
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
            _initial_state(
                [ChatMessage(role=ChatRole.USER, content="meds?")],
                cached_context=_medication_context(),
            )
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
                            "type": "lab_result",
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


@pytest.mark.asyncio
async def test_immunization_question_uses_model_tool_loop() -> None:
    immunization_bundle = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Immunization",
                    "id": "imm-1",
                    "patient": {"reference": f"Patient/{PATIENT}"},
                    "status": "completed",
                    "vaccineCode": {"text": "Influenza vaccine"},
                    "occurrenceDateTime": "2025-10-01",
                    "meta": {"lastUpdated": "2025-10-01T00:00:00+00:00"},
                }
            }
        ],
    }

    call_state = {"count": 0}

    def script(messages: list[dict[str, Any]]) -> LlmChatResult:
        call_state["count"] += 1
        if call_state["count"] == 1:
            tool_names = {
                tool["function"]["name"]
                for tool in messages
                if isinstance(tool.get("function"), dict)
            }
            assert tool_names == set()
            return LlmChatResult(
                content=None,
                tool_calls=[
                    LlmToolCall(
                        tool_call_id="t1",
                        name="get_immunizations",
                        arguments={},
                    )
                ],
            )
        return LlmChatResult(
            content=json.dumps(
                {
                    "narrative": "The chart lists an influenza vaccine on 2025-10-01 [^1].",
                    "facts": [
                        {
                            "type": "immunization",
                            "text": "Influenza vaccine, completed on 2025-10-01",
                            "verbatim_excerpts": [
                                "Influenza vaccine",
                                "completed",
                                "2025-10-01",
                            ],
                            "citations": [
                                {
                                    "resource_type": "Immunization",
                                    "resource_id": "imm-1",
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
        mock.get(f"{FHIR_BASE}/Immunization").mock(
            return_value=httpx.Response(200, json=immunization_bundle)
        )
        graph = build_chat_graph(llm)
        final = await graph.ainvoke(  # type: ignore[attr-defined]
            _initial_state(
                [
                    ChatMessage(
                        role=ChatRole.USER,
                        content="can you give me this patient's immunization records",
                    )
                ]
            )
        )

    assert final["verification_failures"] == []
    assert call_state["count"] == 2
    assert "influenza vaccine" in final["parsed_narrative"]
    assert len(final["verified_facts"]) == 1
    assert final["verified_facts"][0].type.value == "immunization"
    assert final["verified_facts"][0].citations[0].resource_id == "imm-1"
    assert "Influenza vaccine" in final["verified_facts"][0].text
