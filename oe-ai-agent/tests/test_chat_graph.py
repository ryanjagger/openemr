"""End-to-end chat graph tests for the supervisor + workers topology.

The chat graph is no longer linear. Each turn dispatches:

    ensure_chat_context → supervisor ⇄ {extractor, evidence_retriever}
                             ↓
                          finalize → parse_envelope → verify_chat → END

The mocked LLM here drives that flow by inspecting the system prompt
markers ("You are the SUPERVISOR", "EVIDENCE_RETRIEVER", etc.) so each
test can supply: (a) the routing decisions to make, and (b) the final
envelope to emit.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.agent.graph_chat import build_chat_graph
from oe_ai_agent.guidelines.models import GLOBAL_EVIDENCE_PATIENT_ID, GUIDELINE_RESOURCE_TYPE
from oe_ai_agent.llm.client import LlmChatResult, LlmToolCall, LlmUsage
from oe_ai_agent.llm.mock_client import MockLlmClient
from oe_ai_agent.observability import use_trace
from oe_ai_agent.schemas.chat import ChatMessage, ChatRole
from oe_ai_agent.schemas.tool_results import TypedRow

PATIENT = "patient-uuid-1"
FHIR_BASE = "http://fhir.test/apis/default/fhir"
API_BASE = "http://fhir.test/apis/default/api"


def _fhir_routes(mock: respx.MockRouter, *, unindexed: list[dict[str, Any]] | None = None) -> None:
    mock.get(f"{API_BASE}/ai/documents/recent/{PATIENT}").mock(
        return_value=httpx.Response(200, json={"documents": unindexed or []})
    )
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


def _guideline_context() -> list[TypedRow]:
    return [
        TypedRow(
            resource_type=GUIDELINE_RESOURCE_TYPE,
            resource_id="cdc-opioid:1",
            patient_id=GLOBAL_EVIDENCE_PATIENT_ID,
            last_updated=datetime(2022, 11, 4, tzinfo=UTC),
            fields={
                "title": "CDC Clinical Practice Guideline for Prescribing Opioids for Pain",
                "publication_date": "2022-11-04",
                "source": "clinical_guideline_corpus",
            },
            verbatim_excerpt=("Nonopioid therapies are preferred for subacute and chronic pain."),
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


def _detect_role(messages: list[dict[str, Any]]) -> str:
    """Determine which graph node a chat_with_tools call belongs to.

    The supervisor + worker prompts each include a distinctive header in
    the system message; finalize uses the plain chat system prompt with
    no role-specific suffix.
    """
    for msg in messages:
        if msg.get("role") != "system":
            continue
        text = _content_text(msg.get("content"))
        if "You are the SUPERVISOR" in text:
            return "supervisor"
        if "You are the EXTRACTOR worker" in text:
            return "extractor"
        if "You are running as the EVIDENCE_RETRIEVER worker" in text:
            return "evidence"
        return "finalize"
    return "finalize"


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _route(target: str) -> LlmChatResult:
    return LlmChatResult(
        content=json.dumps({"next": target, "reason": "test"}),
        tool_calls=[],
    )


def _stop(content: str = "done") -> LlmChatResult:
    return LlmChatResult(content=content, tool_calls=[])


def _envelope(payload: dict[str, Any]) -> LlmChatResult:
    return LlmChatResult(content=json.dumps(payload), tool_calls=[])


def _supervisor_routes_then_finalize(
    envelope: dict[str, Any],
    *,
    on_evidence: Callable[[list[dict[str, Any]]], LlmChatResult] | None = None,
    on_extractor: Callable[[list[dict[str, Any]]], LlmChatResult] | None = None,
) -> Callable[[list[dict[str, Any]]], LlmChatResult]:
    """Build a scripter that:

    * On the first supervisor call, routes to evidence_retriever (or
      extractor if requested by ``on_extractor``).
    * On subsequent supervisor calls, routes to finalize.
    * Delegates evidence/extractor calls to the supplied handlers (default:
      stop immediately).
    * Returns the envelope on finalize calls.
    """
    state = {"sup_calls": 0}

    def script(messages: list[dict[str, Any]]) -> LlmChatResult:
        role = _detect_role(messages)
        if role == "supervisor":
            state["sup_calls"] += 1
            if state["sup_calls"] == 1 and on_extractor is not None:
                return _route("extractor")
            if state["sup_calls"] == 1:
                return _route("evidence_retriever")
            return _route("finalize")
        if role == "extractor":
            return on_extractor(messages) if on_extractor else _stop("extractor done")
        if role == "evidence":
            return on_evidence(messages) if on_evidence else _stop("evidence done")
        return _envelope(envelope)

    return script


@pytest.mark.asyncio
async def test_happy_path_emits_narrative_and_verified_facts() -> None:
    envelope = {
        "narrative": "She is on lisinopril 10 mg [^1].",
        "facts": [
            {
                "type": "medication",
                "text": "Lisinopril 10 mg, active",
                "verbatim_excerpts": ["Lisinopril 10 mg"],
                "citations": [{"resource_type": "MedicationRequest", "resource_id": "med-1"}],
                "anchor": 1,
            }
        ],
    }
    llm = MockLlmClient(chat_scripted=_supervisor_routes_then_finalize(envelope))

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
    assert final["supervisor_decisions"] == ["evidence_retriever", "finalize"]


@pytest.mark.asyncio
async def test_happy_path_records_step_trace_with_usage() -> None:
    """The trace collector should fill in step records and usage when active."""
    envelope = {
        "narrative": "She is on lisinopril 10 mg [^1].",
        "facts": [
            {
                "type": "medication",
                "text": "Lisinopril 10 mg, active",
                "verbatim_excerpts": ["Lisinopril 10 mg"],
                "citations": [{"resource_type": "MedicationRequest", "resource_id": "med-1"}],
                "anchor": 1,
            }
        ],
    }
    llm = MockLlmClient(
        chat_scripted=_supervisor_routes_then_finalize(envelope),
        default_usage=LlmUsage(
            prompt_tokens=42, completion_tokens=8, total_tokens=50, latency_ms=12
        ),
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
    assert step_names[0] == "ensure_chat_context"
    assert step_names[-1] == "verify_chat"
    assert "supervisor" in step_names
    assert "evidence_retriever" in step_names
    assert "finalize" in step_names
    assert "parse_envelope" in step_names
    # Each LLM call (supervisor x2 + evidence x1 + finalize x1) has a usage
    summary = trace.usage_summary()
    assert summary["prompt_tokens"] >= 42  # at least one LLM call recorded


@pytest.mark.asyncio
async def test_unground_number_in_narrative_is_replaced_with_fallback() -> None:
    envelope = {
        "narrative": "She is on lisinopril 20 mg.",  # 20 is not in facts
        "facts": [
            {
                "type": "medication",
                "text": "Lisinopril 10 mg, active",
                "verbatim_excerpts": ["Lisinopril 10 mg"],
                "citations": [{"resource_type": "MedicationRequest", "resource_id": "med-1"}],
            }
        ],
    }
    llm = MockLlmClient(chat_scripted=_supervisor_routes_then_finalize(envelope))

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
    assert any(f.rule == "tier1_narrative_grounding" for f in final["verification_failures"])


@pytest.mark.asyncio
async def test_guideline_only_narrative_fallback_keeps_cards_without_user_failure() -> None:
    envelope = {
        "narrative": ("The 2022 CDC opioid guideline includes 12 recommendations for pain care."),
        "facts": [
            {
                "type": "guideline",
                "text": ("Nonopioid therapies are preferred for subacute and chronic pain."),
                "verbatim_excerpts": [
                    "Nonopioid therapies are preferred for subacute and chronic pain."
                ],
                "citations": [
                    {
                        "resource_type": GUIDELINE_RESOURCE_TYPE,
                        "resource_id": "cdc-opioid:1",
                    }
                ],
                "anchor": 1,
            }
        ],
    }
    llm = MockLlmClient(chat_scripted=_supervisor_routes_then_finalize(envelope))

    with respx.mock(assert_all_called=False) as mock:
        _fhir_routes(mock)
        graph = build_chat_graph(llm)
        final = await graph.ainvoke(  # type: ignore[attr-defined]
            _initial_state(
                [ChatMessage(role=ChatRole.USER, content="guidelines?")],
                cached_context=_guideline_context(),
            )
        )

    assert len(final["verified_facts"]) == 1
    assert final["parsed_narrative"] == (
        "I found guideline evidence in the verified source cards below."
    )
    assert final["verification_failures"] == []


@pytest.mark.asyncio
async def test_evidence_retriever_drives_lab_trend_tool_loop() -> None:
    """The evidence worker emits a tool call, gets a result, then stops.

    The supervisor then routes to finalize, which emits the envelope.
    """
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

    evidence_calls = {"count": 0}

    def on_evidence(messages: list[dict[str, Any]]) -> LlmChatResult:
        evidence_calls["count"] += 1
        if evidence_calls["count"] == 1:
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
        return _stop("got A1c rows")

    envelope = {
        "narrative": "Most recent A1c was 7.2 [^1].",
        "facts": [
            {
                "type": "lab_result",
                "text": "A1c 7.2 % on 2026-01-15",
                "verbatim_excerpts": ["7.2", "2026-01-15"],
                "citations": [{"resource_type": "Observation", "resource_id": "obs-a1c"}],
                "anchor": 1,
            }
        ],
    }

    llm = MockLlmClient(
        chat_scripted=_supervisor_routes_then_finalize(envelope, on_evidence=on_evidence)
    )

    with respx.mock(assert_all_called=False) as mock:
        _fhir_routes(mock)
        mock.get(f"{FHIR_BASE}/Observation").mock(return_value=httpx.Response(200, json=a1c_bundle))
        graph = build_chat_graph(llm)
        final = await graph.ainvoke(  # type: ignore[attr-defined]
            _initial_state([ChatMessage(role=ChatRole.USER, content="A1c trend?")])
        )

    assert evidence_calls["count"] == 2
    assert any(f.citations[0].resource_id == "obs-a1c" for f in final["verified_facts"])
    assert "7.2" in final["parsed_narrative"]


@pytest.mark.asyncio
async def test_immunization_question_uses_evidence_worker_tool_loop() -> None:
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

    evidence_calls = {"count": 0}

    def on_evidence(messages: list[dict[str, Any]]) -> LlmChatResult:
        evidence_calls["count"] += 1
        if evidence_calls["count"] == 1:
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
        return _stop("got immunizations")

    envelope = {
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
                "citations": [{"resource_type": "Immunization", "resource_id": "imm-1"}],
                "anchor": 1,
            }
        ],
    }
    llm = MockLlmClient(
        chat_scripted=_supervisor_routes_then_finalize(envelope, on_evidence=on_evidence)
    )

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
    assert evidence_calls["count"] == 2
    assert "influenza vaccine" in final["parsed_narrative"]
    assert len(final["verified_facts"]) == 1
    assert final["verified_facts"][0].type.value == "immunization"
    assert final["verified_facts"][0].citations[0].resource_id == "imm-1"
    assert "Influenza vaccine" in final["verified_facts"][0].text


@pytest.mark.asyncio
async def test_supervisor_routes_to_extractor_when_unindexed_lab_present() -> None:
    """Unindexed lab + lab question → supervisor routes to extractor first.

    Post-Phase-5: extract_documents returns no rows directly. The
    supervisor then routes to evidence_retriever, which calls
    get_lab_trend / get_observations to surface the just-ingested
    AI-extracted FHIR Observation (with aiProvenance).
    """
    unindexed_doc = {
        "id": 11,
        "uuid": "doc-uuid-11",
        "filename": "labs_quest_2026.pdf",
        "mimetype": "application/pdf",
        "docdate": "2026-05-01",
        "category_name": "Lab Reports",
        "already_ingested": False,
    }

    extractor_calls = {"count": 0}
    evidence_calls = {"count": 0}
    job_polls = {"count": 0}

    def on_extractor(messages: list[dict[str, Any]]) -> LlmChatResult:
        extractor_calls["count"] += 1
        if extractor_calls["count"] == 1:
            return LlmChatResult(
                content=None,
                tool_calls=[
                    LlmToolCall(
                        tool_call_id="x1",
                        name="extract_documents",
                        arguments={
                            "documents": [{"document_id": 11, "document_type": "lab_report"}]
                        },
                    )
                ],
            )
        return _stop("extracted")

    def on_evidence(messages: list[dict[str, Any]]) -> LlmChatResult:
        evidence_calls["count"] += 1
        if evidence_calls["count"] == 1:
            return LlmChatResult(
                content=None,
                tool_calls=[
                    LlmToolCall(
                        tool_call_id="e1",
                        name="get_lab_trend",
                        arguments={"code_or_text": "LDL"},
                    )
                ],
            )
        return _stop("evidence done")

    envelope = {
        "narrative": "The recent lab report shows LDL of 132 [^1].",
        "facts": [
            {
                "type": "lab_result",
                "text": "LDL 132 mg/dL on 2026-05-01",
                "verbatim_excerpts": ["132", "2026-05-01"],
                "citations": [
                    {
                        "resource_type": "Observation",
                        "resource_id": "obs-ldl-1",
                    }
                ],
                "anchor": 1,
            }
        ],
    }

    state_holder = {"sup_calls": 0}

    def script(messages: list[dict[str, Any]]) -> LlmChatResult:
        role = _detect_role(messages)
        if role == "supervisor":
            state_holder["sup_calls"] += 1
            if state_holder["sup_calls"] == 1:
                return _route("extractor")
            if state_holder["sup_calls"] == 2:
                return _route("evidence_retriever")
            return _route("finalize")
        if role == "extractor":
            return on_extractor(messages)
        if role == "evidence":
            return on_evidence(messages)
        return _envelope(envelope)

    def job_status_handler(request: httpx.Request) -> httpx.Response:
        job_polls["count"] += 1
        return httpx.Response(
            200,
            json={
                "job_id": "job-abc",
                "status": "completed",
                "documents": [{"uuid": "doc-uuid-11"}],
            },
        )

    llm = MockLlmClient(chat_scripted=script)

    with respx.mock(assert_all_called=False) as mock:
        _fhir_routes(mock, unindexed=[unindexed_doc])
        mock.post(f"{API_BASE}/ai/documents/ingest/{PATIENT}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "job_id": "job-abc",
                    "status": "pending",
                    "document_count": 1,
                    "processed_count": 0,
                    "failed_count": 0,
                    "documents": [{"uuid": "doc-uuid-11"}],
                },
            )
        )
        mock.get(f"{API_BASE}/ai/documents/{PATIENT}/jobs/job-abc").mock(
            side_effect=job_status_handler,
        )
        mock.get(f"{FHIR_BASE}/Observation").mock(
            return_value=httpx.Response(
                200,
                json={
                    "resourceType": "Bundle",
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Observation",
                                "id": "obs-ldl-1",
                                "subject": {"reference": f"Patient/{PATIENT}"},
                                "status": "final",
                                "category": [
                                    {"coding": [{"code": "laboratory"}]}
                                ],
                                "code": {"text": "LDL"},
                                "effectiveDateTime": "2026-05-01",
                                "valueQuantity": {"value": 132, "unit": "mg/dL"},
                                "extension": [
                                    {
                                        "url": "https://openemr.org/fhir/StructureDefinition/ai-provenance",
                                        "extension": [
                                            {"url": "documentId", "valueString": "11"},
                                            {"url": "snippet", "valueString": "LDL 132 mg/dL"},
                                        ],
                                    }
                                ],
                            }
                        }
                    ],
                },
            )
        )

        graph = build_chat_graph(llm)
        final = await graph.ainvoke(  # type: ignore[attr-defined]
            _initial_state(
                [
                    ChatMessage(
                        role=ChatRole.USER,
                        content="What's in the recent lab report uploaded?",
                    )
                ]
            )
        )

    assert final["supervisor_decisions"] == ["extractor", "evidence_retriever", "finalize"]
    assert final["extractor_runs"] == 1
    assert job_polls["count"] >= 1
    assert any(f.citations[0].resource_id == "obs-ldl-1" for f in final["verified_facts"])
    assert "132" in final["parsed_narrative"]


@pytest.mark.asyncio
async def test_supervisor_skips_extractor_when_unindexed_list_empty() -> None:
    """No unindexed docs → supervisor never picks extractor route."""
    envelope = {
        "narrative": "She is on lisinopril 10 mg [^1].",
        "facts": [
            {
                "type": "medication",
                "text": "Lisinopril 10 mg, active",
                "verbatim_excerpts": ["Lisinopril 10 mg"],
                "citations": [{"resource_type": "MedicationRequest", "resource_id": "med-1"}],
                "anchor": 1,
            }
        ],
    }
    seen_routes: list[str] = []

    def script(messages: list[dict[str, Any]]) -> LlmChatResult:
        role = _detect_role(messages)
        seen_routes.append(role)
        if role == "supervisor":
            # Try to pick extractor — supervisor guardrail should override.
            return _route("extractor")
        if role == "evidence":
            return _stop("evidence done")
        if role == "extractor":
            pytest.fail("extractor should not run when unindexed list is empty")
        return _envelope(envelope)

    llm = MockLlmClient(chat_scripted=script)

    with respx.mock(assert_all_called=False) as mock:
        _fhir_routes(mock, unindexed=[])
        graph = build_chat_graph(llm)
        final = await graph.ainvoke(  # type: ignore[attr-defined]
            _initial_state(
                [ChatMessage(role=ChatRole.USER, content="meds?")],
                cached_context=_medication_context(),
            )
        )

    assert "extractor" not in seen_routes
    assert final["extractor_runs"] == 0
    assert len(final["verified_facts"]) == 1
