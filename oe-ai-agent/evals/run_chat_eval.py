"""Eval runner for the chat agent.

Calls the live LLM against turn-based fixtures with FHIR responses mocked
via respx. Writes one JSONL row per chat turn so prompt/model A/B
comparisons can inspect tool choice, cache reuse, verifier drops, and the
final grounded answer.

Usage::

    export ANTHROPIC_API_KEY=sk-ant-...
    uv run python evals/run_chat_eval.py --label baseline

Fixtures live in ``evals/chat_fixtures/*.json``. Each fixture contains a
``fhir`` map plus a ``turns`` list. Date tokens of the form
``{{TODAY-15D}}`` are substituted at load time so fixtures stay evergreen.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import respx

try:
    from evals.common import (
        EVAL_FHIR_BASE,
        EVAL_PATIENT_UUID,
        default_output_path,
        drop_counts,
        install_fhir_routes,
        load_fixtures,
        type_counts,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from common import (  # type: ignore[no-redef]
        EVAL_FHIR_BASE,
        EVAL_PATIENT_UUID,
        default_output_path,
        drop_counts,
        install_fhir_routes,
        load_fixtures,
        type_counts,
    )

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.agent.graph_chat import build_chat_graph
from oe_ai_agent.llm import LiteLLMClient, LlmClient, MockLlmClient
from oe_ai_agent.llm.client import LlmChatResult, LlmToolCall
from oe_ai_agent.observability import use_trace
from oe_ai_agent.schemas.brief import VerificationFailure
from oe_ai_agent.schemas.chat import ChatFact, ChatMessage, ChatRole
from oe_ai_agent.schemas.tool_results import ToolError, TypedRow

DEFAULT_CHAT_FIXTURES_DIR = Path(__file__).parent / "chat_fixtures"
EVAL_API_BASE = EVAL_FHIR_BASE[: -len("/fhir")] + "/api"

_EMPTY_BUNDLE_RESOURCE_TYPES = (
    "AllergyIntolerance",
    "Appointment",
    "CarePlan",
    "Condition",
    "DiagnosticReport",
    "DocumentReference",
    "Encounter",
    "Goal",
    "Immunization",
    "MedicationRequest",
    "Observation",
    "Procedure",
    "ServiceRequest",
)

_TOOL_RULES: tuple[tuple[tuple[str, ...], str, dict[str, Any]], ...] = (
    (
        ("indexed lab", "uploaded lab", "external lab", "scanned lab", "pdf lab"),
        "get_observations",
        {"category": "laboratory"},
    ),
    (
        ("intake", "questionnaire", "patient reported", "uploaded form"),
        "get_questionnaire_responses",
        {},
    ),
    (
        ("stopped", "discontinued", "medication history", "past medication"),
        "get_medication_history",
        {},
    ),
    (("medication", "medications", "medicine", "lisinopril", "stop"), "get_active_medications", {}),
    (("allerg",), "get_allergies", {}),
    (("problem", "diagnose", "diagnosis", "diagnoses", "condition"), "get_active_problems", {}),
    (
        ("a1c", "hemoglobin", "creatinine", "ldl", "lab trend"),
        "get_lab_trend",
        {"code_or_text": "hemoglobin a1c"},
    ),
    (
        ("blood pressure", "heart rate", "vital", "weight", "bmi", "observation"),
        "get_observations",
        {},
    ),
    (("immunization", "vaccine", "vaccination", "flu shot"), "get_immunizations", {}),
    (("appointment", "upcoming"), "get_appointments", {}),
    (("goal", "care plan", "careplan"), "get_care_plan_goals", {}),
    (("visit", "encounter"), "get_recent_encounters", {}),
    (("note", "notes", "documentreference"), "get_recent_notes", {}),
    (("order", "orders", "servicerequest"), "get_orders", {}),
    (("procedure", "surgery"), "get_procedures", {}),
    (("demographic", "birth", "dob", "gender", "name"), "get_demographics", {}),
)

_RESOURCE_TYPE_RULES: tuple[tuple[tuple[str, ...], frozenset[str]], ...] = (
    (
        ("indexed lab", "uploaded lab", "external lab", "scanned lab", "pdf lab"),
        frozenset({"Observation", "DiagnosticReport"}),
    ),
    (
        ("intake", "questionnaire", "patient reported", "uploaded form"),
        frozenset({"QuestionnaireResponse"}),
    ),
    (
        ("stopped", "discontinued", "medication history", "past medication"),
        frozenset({"MedicationRequest", "DocumentReference"}),
    ),
    (
        ("medication", "medications", "medicine", "lisinopril", "stop"),
        frozenset({"MedicationRequest"}),
    ),
    (("allerg",), frozenset({"AllergyIntolerance"})),
    (("problem", "diagnose", "diagnosis", "diagnoses", "condition"), frozenset({"Condition"})),
    (
        ("a1c", "hemoglobin", "creatinine", "ldl", "lab trend"),
        frozenset({"Observation", "DiagnosticReport"}),
    ),
    (
        ("blood pressure", "heart rate", "vital", "weight", "bmi", "observation"),
        frozenset({"Observation"}),
    ),
    (("immunization", "vaccine", "vaccination", "flu shot"), frozenset({"Immunization"})),
    (("appointment", "upcoming"), frozenset({"Appointment"})),
    (("goal", "care plan", "careplan"), frozenset({"CarePlan", "Goal"})),
    (("visit", "encounter"), frozenset({"Encounter"})),
    (
        ("note", "notes", "documentreference"),
        frozenset({"DocumentReference"}),
    ),
    (("order", "orders", "servicerequest"), frozenset({"ServiceRequest"})),
    (("procedure", "surgery"), frozenset({"Procedure"})),
    (("demographic", "birth", "dob", "gender", "name"), frozenset({"Patient"})),
)

_DOCUMENT_RE = re.compile(
    r"id=(?P<id>\d+)\s+uuid=(?P<uuid>\S+)\s+type_hint=(?P<type>\S+)\s+filename=(?P<filename>\S+)"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_CHAT_FIXTURES_DIR)
    parser.add_argument("--label", required=True, help="Free-form label for this run")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--provider",
        choices=["anthropic", "mock"],
        default="anthropic",
        help="`mock` is for harness debugging only — not a real eval",
    )
    parser.add_argument("--model", default="anthropic/claude-sonnet-4-6")
    parser.add_argument(
        "--only",
        action="append",
        help="Run only fixtures whose stem matches one of these (repeatable)",
    )
    parser.add_argument(
        "--fail-on-expectations",
        action="store_true",
        help=(
            "Exit nonzero when any non-known-limitation turn has a runner error, "
            "parse error, or failed boolean expectation."
        ),
    )
    args = parser.parse_args()

    fixtures = load_fixtures(args.fixtures, only=args.only)
    if not fixtures:
        print(f"no fixtures found under {args.fixtures}", file=sys.stderr)
        return 1

    llm = _build_llm(args.provider, args.model)
    output_path = args.output or default_output_path(args.label, prefix="chat_")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(
        f"running {len(fixtures)} chat fixture(s) | model={llm.model_id} | label={args.label}",
        file=sys.stderr,
    )
    print(f"writing → {output_path}", file=sys.stderr)

    started = time.perf_counter()
    summary = Counter[str]()
    with output_path.open("w", encoding="utf-8") as out:
        for fixture in fixtures:
            rows = asyncio.run(_run_fixture(fixture, llm, args.label))
            for row in rows:
                out.write(json.dumps(row) + "\n")
                out.flush()
                summary["turns"] += 1
                summary["facts_verified"] += row["facts_verified"]
                failed = _row_failed(row)
                if failed and row["known_limitation"]:
                    summary["known_limitation_failures"] += 1
                elif failed:
                    summary["turns_with_failed_expectations"] += 1
                mark = " (known limitation)" if row["known_limitation"] else ""
                print(
                    f"  {row['fixture_id']} turn {row['turn_index']}: "
                    f"{row['facts_verified']} verified fact(s), "
                    f"{row['tool_call_count']} tool call(s) "
                    f"({row['duration_ms']} ms){mark}",
                    file=sys.stderr,
                )

    elapsed = time.perf_counter() - started
    print(
        f"done in {elapsed:.1f}s — "
        f"{summary['facts_verified']} facts across {summary['turns']} turns, "
        f"{summary['turns_with_failed_expectations']} failed expectations "
        f"({summary['known_limitation_failures']} known limitations)",
        file=sys.stderr,
    )
    if args.fail_on_expectations and summary["turns_with_failed_expectations"]:
        return 1
    return 0


def _build_llm(provider: str, model: str) -> LlmClient:
    if provider == "mock":
        return MockLlmClient(chat_scripted=_mock_chat_script)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "ANTHROPIC_API_KEY is required for --provider anthropic. "
            "Use --provider mock to debug the harness without it.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return LiteLLMClient(model=model, api_key=api_key)


async def _run_fixture(
    fixture: dict[str, Any],
    llm: LlmClient,
    label: str,
) -> list[dict[str, Any]]:
    history = _messages_from_fixture(fixture.get("history", []))
    cached_context: list[TypedRow] = []
    rows: list[dict[str, Any]] = []
    graph = build_chat_graph(llm)

    with respx.mock(assert_all_called=False, assert_all_mocked=False) as router:
        fhir = fixture.get("fhir", {})
        api = fixture.get("api", {})
        install_fhir_routes(router, fhir)
        _install_empty_chat_routes(router, fhir)
        _install_api_routes(router, api)
        _install_empty_api_routes(router, api)

        for index, turn in enumerate(fixture.get("turns", []), start=1):
            row, state = await _run_turn(
                fixture=fixture,
                turn=turn,
                turn_index=index,
                graph=graph,
                llm=llm,
                label=label,
                history=history,
                cached_context=cached_context,
            )
            rows.append(row)
            if state is None:
                continue
            user_message = ChatMessage(role=ChatRole.USER, content=str(turn["user"]))
            assistant_message = ChatMessage(
                role=ChatRole.ASSISTANT,
                content=state.parsed_narrative,
            )
            history.extend([user_message, assistant_message])
            cached_context = state.cached_context

    return rows


async def _run_turn(
    *,
    fixture: dict[str, Any],
    turn: dict[str, Any],
    turn_index: int,
    graph: object,
    llm: LlmClient,
    label: str,
    history: list[ChatMessage],
    cached_context: list[TypedRow],
) -> tuple[dict[str, Any], ChatState | None]:
    fixture_id = fixture["__id__"]
    fixture_label = fixture.get("label", "")
    turn_label = turn.get("label", "")
    expectations = turn.get("expectations", {})
    known_limitation = bool(fixture.get("known_limitation", False)) or bool(
        turn.get("known_limitation", False)
    )
    started = time.perf_counter()
    cache_rows_before = len(cached_context)

    parsed_facts: list[ChatFact] = []
    verified_facts: list[ChatFact] = []
    failures: list[VerificationFailure] = []
    fetch_errors: list[ToolError] = []
    narrative = ""
    parse_error: str | None = None
    error: str | None = None
    trace_steps: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    state: ChatState | None = None

    try:
        turn_history = _turn_history(history, turn)
        turn_history.append(ChatMessage(role=ChatRole.USER, content=str(turn["user"])))
        initial = ChatState(
            patient_uuid=EVAL_PATIENT_UUID,
            fhir_base_url=EVAL_FHIR_BASE,
            bearer_token="eval-bearer-stub",
            request_id=f"chat-eval-{fixture_id}-{turn_index}",
            conversation_id=f"chat-eval-{fixture_id}",
            history=turn_history,
            cached_context=cached_context,
        )
        async with use_trace() as trace:
            final = await graph.ainvoke(initial)  # type: ignore[attr-defined]
        trace_steps = trace.to_list()
        usage = trace.usage_summary()
        state = ChatState.model_validate(final)
        parsed_facts = state.parsed_facts
        verified_facts = state.verified_facts
        failures = state.verification_failures
        fetch_errors = state.fetch_errors
        narrative = state.parsed_narrative
        parse_error = state.parse_error
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    tool_calls = _tool_calls_from_trace(trace_steps)
    duration_ms = int((time.perf_counter() - started) * 1000)
    row = {
        "ts": datetime.now(tz=UTC).isoformat(),
        "label": label,
        "model_id": llm.model_id,
        "fixture_id": fixture_id,
        "fixture_label": fixture_label,
        "turn_index": turn_index,
        "turn_label": turn_label,
        "known_limitation": known_limitation,
        "error": error,
        "parse_error": parse_error,
        "narrative": narrative,
        "facts_emitted": len(parsed_facts),
        "facts_verified": len(verified_facts),
        "drop_count_by_rule": drop_counts(failures),
        "type_counts_verified": type_counts(verified_facts),
        "tool_calls": tool_calls,
        "tool_call_count": len(tool_calls),
        "tool_call_count_by_tool": dict(Counter(tool_calls)),
        "llm_iterations": _llm_iterations(trace_steps),
        "cache_rows_before": cache_rows_before,
        "cache_rows_after": len(state.cached_context) if state is not None else cache_rows_before,
        "fetch_errors": [err.model_dump(mode="json") for err in fetch_errors],
        "usage": usage,
        "duration_ms": duration_ms,
        "expectations_met": _check_chat_expectations(
            narrative=narrative,
            facts=verified_facts,
            failures=failures,
            tool_calls=tool_calls,
            expectations=expectations,
        ),
        "facts": [_fact_to_dict(fact) for fact in verified_facts],
        "failures": [failure.model_dump() for failure in failures],
        "steps": trace_steps,
    }
    return row, state


def _messages_from_fixture(raw: object) -> list[ChatMessage]:
    if not isinstance(raw, list):
        return []
    return [ChatMessage.model_validate(item) for item in raw]


def _turn_history(history: list[ChatMessage], turn: dict[str, Any]) -> list[ChatMessage]:
    if "history" in turn:
        return _messages_from_fixture(turn["history"])
    return list(history)


def _install_empty_chat_routes(
    router: respx.MockRouter,
    fhir: dict[str, Any],
) -> None:
    if "Patient" not in fhir:
        router.get(f"{EVAL_FHIR_BASE}/Patient/{EVAL_PATIENT_UUID}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "resourceType": "Patient",
                    "id": EVAL_PATIENT_UUID,
                    "meta": {"lastUpdated": datetime.now(tz=UTC).isoformat()},
                },
            ),
        )

    for resource_type in _EMPTY_BUNDLE_RESOURCE_TYPES:
        if resource_type in fhir:
            continue
        router.get(f"{EVAL_FHIR_BASE}/{resource_type}").mock(
            return_value=httpx.Response(200, json={"resourceType": "Bundle", "entry": []}),
        )


def _install_api_routes(
    router: respx.MockRouter,
    api: object,
) -> None:
    if not isinstance(api, dict):
        return
    for raw_key, raw_body in api.items():
        if not isinstance(raw_key, str):
            continue
        method, path = _parse_api_route_key(raw_key)
        route = getattr(router, method.lower(), None)
        if route is None:
            continue
        status_code = 200
        body = raw_body
        if isinstance(raw_body, dict) and "json" in raw_body:
            body = raw_body["json"]
            raw_status = raw_body.get("status_code")
            if isinstance(raw_status, int):
                status_code = raw_status
        route(_api_url(path)).mock(return_value=httpx.Response(status_code, json=body))


def _install_empty_api_routes(
    router: respx.MockRouter,
    api: object,
) -> None:
    configured = _configured_api_keys(api)
    defaults: dict[str, dict[str, Any]] = {
        f"GET /ai/documents/recent/{EVAL_PATIENT_UUID}": {"documents": []},
        f"GET /ai/documents/indexed/{EVAL_PATIENT_UUID}/document": {"documents": []},
        f"GET /ai/documents/indexed-facts/{EVAL_PATIENT_UUID}/document": {"facts": []},
    }
    for key, body in defaults.items():
        method, path = _parse_api_route_key(key)
        if (method, _normalize_api_path(path)) in configured:
            continue
        getattr(router, method.lower())(_api_url(path)).mock(
            return_value=httpx.Response(200, json=body)
        )


def _configured_api_keys(api: object) -> set[tuple[str, str]]:
    if not isinstance(api, dict):
        return set()
    keys: set[tuple[str, str]] = set()
    for raw_key in api:
        if not isinstance(raw_key, str):
            continue
        method, path = _parse_api_route_key(raw_key)
        keys.add((method, _normalize_api_path(path)))
    return keys


def _parse_api_route_key(value: str) -> tuple[str, str]:
    stripped = value.strip()
    if " " in stripped:
        raw_method, raw_path = stripped.split(maxsplit=1)
        method = raw_method.upper()
        path = raw_path
    else:
        method = "GET"
        path = stripped
    if method not in {"GET", "POST"}:
        method = "GET"
    return method, path


def _api_url(path: str) -> str:
    return f"{EVAL_API_BASE}/{_normalize_api_path(path)}"


def _normalize_api_path(path: str) -> str:
    return path.strip().lstrip("/")


def _tool_calls_from_trace(trace_steps: list[dict[str, Any]]) -> list[str]:
    calls: list[str] = []
    for record in trace_steps:
        if record.get("name") != "tool_call":
            continue
        attrs = record.get("attrs", {})
        if isinstance(attrs, dict) and isinstance(attrs.get("tool"), str):
            calls.append(attrs["tool"])
    return calls


def _llm_iterations(trace_steps: list[dict[str, Any]]) -> int:
    for record in trace_steps:
        if record.get("name") != "llm_turn":
            continue
        attrs = record.get("attrs", {})
        if isinstance(attrs, dict) and isinstance(attrs.get("iterations"), int):
            return attrs["iterations"]
    return 0


def _fact_to_dict(fact: ChatFact) -> dict[str, Any]:
    return {
        "type": fact.type.value,
        "text": fact.text,
        "verbatim_excerpts": list(fact.verbatim_excerpts),
        "citations": [
            {"resource_type": c.resource_type, "resource_id": c.resource_id} for c in fact.citations
        ],
        "anchor": fact.anchor,
    }


def _check_chat_expectations(  # noqa: PLR0912 - flat fixture expectation keys.
    *,
    narrative: str,
    facts: list[ChatFact],
    failures: list[VerificationFailure],
    tool_calls: list[str],
    expectations: dict[str, Any],
) -> dict[str, bool]:
    fact_types = {fact.type.value for fact in facts}
    cited_ids = {citation.resource_id for fact in facts for citation in fact.citations}
    fact_text_blob = " ".join(fact.text for fact in facts).lower()
    narrative_blob = narrative.lower()
    drop_counter = Counter(failure.rule for failure in failures)
    tool_counter = Counter(tool_calls)
    result: dict[str, bool] = {}

    if "min_verified_facts" in expectations:
        result["min_verified_facts"] = len(facts) >= int(expectations["min_verified_facts"])
    if "max_verified_facts" in expectations:
        result["max_verified_facts"] = len(facts) <= int(expectations["max_verified_facts"])
    if "expected_fact_types_present" in expectations:
        wanted = set(expectations["expected_fact_types_present"])
        result["expected_fact_types_present"] = wanted.issubset(fact_types)
    if "expected_fact_types_absent" in expectations:
        unwanted = set(expectations["expected_fact_types_absent"])
        result["expected_fact_types_absent"] = unwanted.isdisjoint(fact_types)
    if "expected_citations" in expectations:
        wanted_ids = set(expectations["expected_citations"])
        result["expected_citations"] = wanted_ids.issubset(cited_ids)
    if "forbidden_citations" in expectations:
        unwanted_ids = set(expectations["forbidden_citations"])
        result["forbidden_citations"] = unwanted_ids.isdisjoint(cited_ids)
    if "narrative_must_contain" in expectations:
        needles = [str(value).lower() for value in expectations["narrative_must_contain"]]
        result["narrative_must_contain"] = all(needle in narrative_blob for needle in needles)
    if "narrative_must_not_contain" in expectations:
        forbidden = [str(value).lower() for value in expectations["narrative_must_not_contain"]]
        result["narrative_must_not_contain"] = all(
            needle not in narrative_blob for needle in forbidden
        )
    if "facts_must_contain" in expectations:
        needles = [str(value).lower() for value in expectations["facts_must_contain"]]
        result["facts_must_contain"] = all(needle in fact_text_blob for needle in needles)
    if "facts_must_not_contain" in expectations:
        forbidden = [str(value).lower() for value in expectations["facts_must_not_contain"]]
        result["facts_must_not_contain"] = all(needle not in fact_text_blob for needle in forbidden)
    if "expected_drop_rules" in expectations:
        wanted_drops: dict[str, int] = expectations["expected_drop_rules"]
        result["expected_drop_rules"] = all(
            drop_counter.get(rule, 0) >= int(count) for rule, count in wanted_drops.items()
        )
    if "expected_tools_called" in expectations:
        wanted_tools = set(expectations["expected_tools_called"])
        result["expected_tools_called"] = wanted_tools.issubset(set(tool_calls))
    if "forbidden_tools_called" in expectations:
        forbidden_tools = set(expectations["forbidden_tools_called"])
        result["forbidden_tools_called"] = forbidden_tools.isdisjoint(set(tool_calls))
    if "max_tool_calls" in expectations:
        result["max_tool_calls"] = len(tool_calls) <= int(expectations["max_tool_calls"])
    if "expected_tool_call_counts" in expectations:
        expected_counts: dict[str, int] = expectations["expected_tool_call_counts"]
        result["expected_tool_call_counts"] = all(
            tool_counter.get(tool_name, 0) == int(count)
            for tool_name, count in expected_counts.items()
        )

    return result


def _mock_chat_script(messages: list[dict[str, Any]]) -> LlmChatResult:  # noqa: PLR0911
    role = _mock_role(messages)
    question = _question_from_messages(messages)

    if role == "supervisor":
        return LlmChatResult(
            content=json.dumps(_mock_supervisor_route(messages, question)),
            tool_calls=[],
        )
    if role == "extractor":
        return _mock_extractor_result(messages, question)
    if role == "finalize":
        rows = _select_rows_for_question(_rows_from_messages(messages), question)
        return LlmChatResult(
            content=json.dumps(_mock_envelope_for_rows(rows, question)),
            tool_calls=[],
        )

    rows = _rows_from_messages(messages)
    if rows:
        selected = _select_rows_for_question(rows, question)
        if selected:
            return LlmChatResult(
                content=json.dumps(_mock_envelope_for_rows(selected, question)),
                tool_calls=[],
            )
    if _has_tool_result(messages):
        return LlmChatResult(
            content=json.dumps(_mock_empty_envelope(question)),
            tool_calls=[],
        )

    tool_name, arguments = _tool_for_question(question)
    if tool_name is None:
        return LlmChatResult(
            content=json.dumps(_mock_empty_envelope(question)),
            tool_calls=[],
        )
    return LlmChatResult(
        content=None,
        tool_calls=[
            LlmToolCall(
                tool_call_id="mock-tool-1",
                name=tool_name,
                arguments=arguments,
            )
        ],
    )


def _mock_role(messages: list[dict[str, Any]]) -> str:
    first_content = ""
    for message in messages:
        if message.get("role") != "system":
            continue
        content = message.get("content")
        if isinstance(content, str):
            first_content = content
            break
    if "You are the SUPERVISOR" in first_content:
        return "supervisor"
    if "You are the EXTRACTOR worker" in first_content:
        return "extractor"
    if "EVIDENCE_RETRIEVER worker" in first_content:
        return "evidence"
    if "OpenEMR chart-context chat assistant" in first_content:
        return "finalize"
    return "evidence"


def _mock_supervisor_route(messages: list[dict[str, Any]], question: str) -> dict[str, str]:
    prompt = _joined_message_content(messages)
    extractor_runs = _worker_run_count(prompt, "extractor_runs")
    evidence_runs = _worker_run_count(prompt, "evidence_runs")
    has_context = "CACHED CONTEXT SUMMARY:\n  (empty)" not in prompt

    if (
        extractor_runs == 0
        and _has_unindexed_documents(prompt)
        and _question_needs_extraction(question)
    ):
        return {"next": "extractor", "reason": "relevant unindexed document exists"}
    if not has_context and evidence_runs == 0 and _tool_for_question(question)[0] is not None:
        return {"next": "evidence_retriever", "reason": "chart evidence is needed"}
    return {"next": "finalize", "reason": "context is sufficient or unavailable"}


def _mock_extractor_result(
    messages: list[dict[str, Any]],
    question: str,
) -> LlmChatResult:
    if _has_tool_result(messages):
        return LlmChatResult(content="Extraction complete.", tool_calls=[])

    documents = _documents_from_prompt(_joined_message_content(messages))
    target_type = _target_document_type(question)
    if target_type is None:
        return LlmChatResult(content="No relevant document was selected.", tool_calls=[])

    selections = [
        {"document_id": document_id, "document_type": document_type}
        for document_id, document_type, filename in documents
        if document_type == target_type or target_type in filename.lower()
    ]
    if not selections:
        return LlmChatResult(content="No relevant document was selected.", tool_calls=[])
    return LlmChatResult(
        content=None,
        tool_calls=[
            LlmToolCall(
                tool_call_id="mock-extract-1",
                name="extract_documents",
                arguments={"documents": selections[:2]},
            )
        ],
    )


def _worker_run_count(prompt: str, key: str) -> int:
    match = re.search(rf"\b{re.escape(key)}=(\d+)", prompt)
    return int(match.group(1)) if match is not None else 0


def _has_unindexed_documents(prompt: str) -> bool:
    return bool(_DOCUMENT_RE.search(prompt))


def _question_needs_extraction(question: str) -> bool:
    lowered = question.lower()
    return _target_document_type(question) is not None and _contains_any(
        lowered,
        ("unindexed", "new upload", "recent upload", "uploaded"),
    )


def _target_document_type(question: str) -> str | None:
    lowered = question.lower()
    if _contains_any(lowered, ("intake", "questionnaire", "form")):
        return "intake_form"
    if _contains_any(lowered, ("lab", "a1c", "ldl", "creatinine", "result")):
        return "lab_report"
    return None


def _documents_from_prompt(prompt: str) -> list[tuple[int, str, str]]:
    documents: list[tuple[int, str, str]] = []
    for match in _DOCUMENT_RE.finditer(prompt):
        type_hint = match.group("type")
        if type_hint not in {"lab_report", "intake_form"}:
            continue
        documents.append((int(match.group("id")), type_hint, match.group("filename")))
    return documents


def _joined_message_content(messages: list[dict[str, Any]]) -> str:
    return "\n".join(
        content for message in messages if isinstance((content := message.get("content")), str)
    )


def _rows_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if message.get("role") == "tool":
            for row in _rows_from_tool_payload(content):
                rows_by_key[(row["resource_type"], row["resource_id"])] = row
            continue
        for row in _rows_from_context_prompt(content):
            rows_by_key[(row["resource_type"], row["resource_id"])] = row
    return list(rows_by_key.values())


def _rows_from_tool_payload(content: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return []
    raw_rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(raw_rows, list):
        return []
    return [row for row in raw_rows if _is_row_dict(row)]


def _rows_from_context_prompt(content: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{") or '"id"' not in stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        raw_id = payload.get("id")
        if not isinstance(raw_id, str) or "/" not in raw_id:
            continue
        resource_type, resource_id = raw_id.split("/", maxsplit=1)
        rows.append(
            {
                "resource_type": resource_type,
                "resource_id": resource_id,
                "last_updated": payload.get("last_updated"),
                "fields": payload.get("fields", {}),
            }
        )
    return rows


def _is_row_dict(value: object) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("resource_type"), str)
        and isinstance(value.get("resource_id"), str)
        and isinstance(value.get("fields"), dict)
    )


def _has_tool_result(messages: list[dict[str, Any]]) -> bool:
    return any(message.get("role") == "tool" for message in messages)


def _last_user_question(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and not content.startswith("PATIENT:"):
            return content
    return ""


def _question_from_messages(messages: list[dict[str, Any]]) -> str:
    direct = _last_user_question(messages)
    if direct:
        return direct
    prompt = _joined_message_content(messages)
    for marker in ("LAST USER MESSAGE:", "USER QUESTION:"):
        extracted = _extract_prompt_block(prompt, marker)
        if extracted:
            return extracted
    return ""


def _extract_prompt_block(prompt: str, marker: str) -> str:
    start = prompt.find(marker)
    if start < 0:
        return ""
    rest = prompt[start + len(marker) :]
    lines: list[str] = []
    for line in rest.splitlines():
        if lines and line.startswith(("UNINDEXED DOCUMENTS", "CACHED CONTEXT", "CURRENT CONTEXT")):
            break
        if not lines and not line.strip():
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _tool_for_question(question: str) -> tuple[str | None, dict[str, Any]]:
    lowered = question.lower()
    for keywords, tool_name, arguments in _TOOL_RULES:
        if _contains_any(lowered, keywords):
            return tool_name, dict(arguments)
    return None, {}


def _select_rows_for_question(
    rows: list[dict[str, Any]],
    question: str,
) -> list[dict[str, Any]]:
    wanted_types = _resource_types_for_question(question)
    selected = (
        rows if not wanted_types else [row for row in rows if row["resource_type"] in wanted_types]
    )
    if "latest" in question.lower() and selected:
        return [_latest_row(selected)]
    return selected


def _latest_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return max(rows, key=lambda row: str(row.get("last_updated") or ""))


def _resource_types_for_question(question: str) -> frozenset[str]:
    lowered = question.lower()
    for keywords, resource_types in _RESOURCE_TYPE_RULES:
        if _contains_any(lowered, keywords):
            return resource_types
    return frozenset()


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _row_failed(row: dict[str, Any]) -> bool:
    return bool(row["error"] or row["parse_error"]) or not all(row["expectations_met"].values())


def _mock_empty_envelope(question: str) -> dict[str, Any]:
    if _is_refusal_question(question):
        narrative = (
            "I can't determine that from the available chart context; "
            "changes, diagnoses, and urgent decisions require clinician judgment."
        )
    else:
        narrative = "The available chart results do not contain that information."
    return {"narrative": narrative, "facts": []}


def _mock_envelope_for_rows(rows: list[dict[str, Any]], question: str) -> dict[str, Any]:
    facts = [
        fact
        for index, row in enumerate(rows, start=1)
        if (fact := _mock_fact_for_row(row, index, question)) is not None
    ]
    if not facts:
        return _mock_empty_envelope(question)
    first_text = facts[0]["text"]
    if _is_refusal_question(question):
        narrative = (
            "I can summarize the charted information, but changes or diagnoses "
            f"require clinician judgment. The chart shows {first_text} [^1]."
        )
        return {"narrative": narrative, "facts": facts}
    if len(facts) == 1:
        narrative = f"The chart shows {first_text} [^1]."
    else:
        narrative = f"The chart shows {first_text} [^1] and additional verified facts below."
    return {"narrative": narrative, "facts": facts}


def _is_refusal_question(question: str) -> bool:
    lowered = question.lower()
    return _contains_any(
        lowered,
        (
            "should i",
            "should she",
            "stop",
            "start",
            "diagnose",
            "diagnosis",
            "emergency",
            "urgent",
            "legal",
            "outside the chart",
        ),
    )


def _mock_fact_for_row(
    row: dict[str, Any],
    anchor: int,
    question: str,
) -> dict[str, Any] | None:
    resource_type = row["resource_type"]
    resource_id = row["resource_id"]
    fields = row.get("fields", {})
    citation = [{"resource_type": resource_type, "resource_id": resource_id}]
    builder = _MOCK_FACT_BUILDERS.get(resource_type)
    if builder is None:
        return None
    return builder(fields, citation, anchor, question)


CitationPayload = list[dict[str, str]]
MockFactBuilder = Callable[[dict[str, Any], CitationPayload, int, str], dict[str, Any]]


def _medication_fact(
    fields: dict[str, Any],
    citation: CitationPayload,
    anchor: int,
    question: str,
) -> dict[str, Any]:
    med = _coding_text(fields.get("medicationCodeableConcept")) or "Medication"
    authored = _clean_string(fields.get("authoredOn"))
    status = _clean_string(fields.get("status"))
    text = f"{med} authored on {authored}" if authored else med
    fact_type = "medication"
    if status and status.lower() not in {"active", "unknown"}:
        authored_text = f"authored on {authored}" if authored else ""
        text = " ".join(part for part in [med, status, authored_text] if part)
        fact_type = "medication_change"
    elif _contains_any(question.lower(), ("stopped", "discontinued", "changed")):
        fact_type = "medication_change"
    return _fact(fact_type, text, [med, status, authored], citation, anchor)


def _observation_fact(
    fields: dict[str, Any],
    citation: CitationPayload,
    anchor: int,
    question: str,
) -> dict[str, Any]:
    del question
    label = _coding_text(fields.get("code")) or "Observation"
    quantity = _quantity_text(fields.get("valueQuantity"))
    effective = _clean_string(fields.get("effectiveDateTime"))
    dated = f"on {effective}" if effective else ""
    text = " ".join(part for part in [label, quantity, dated] if part)
    if _is_lab_result(label, fields):
        fact_type = "lab_result"
    elif _is_vital_sign(label, fields):
        fact_type = "vital_sign"
    elif "code status" in label.lower():
        fact_type = "code_status"
    else:
        fact_type = "observation"
    return _fact(fact_type, text, [label, quantity, effective], citation, anchor)


def _immunization_fact(
    fields: dict[str, Any],
    citation: CitationPayload,
    anchor: int,
    question: str,
) -> dict[str, Any]:
    del question
    vaccine = _coding_text(fields.get("vaccineCode")) or "Immunization"
    status = _clean_string(fields.get("status"))
    occurred = _clean_string(fields.get("occurrenceDateTime"))
    dated = f"on {occurred}" if occurred else ""
    text = " ".join(part for part in [vaccine, status, dated] if part)
    return _fact("immunization", text, [vaccine, status, occurred], citation, anchor)


def _allergy_fact(
    fields: dict[str, Any],
    citation: CitationPayload,
    anchor: int,
    question: str,
) -> dict[str, Any]:
    del question
    allergy = _coding_text(fields.get("code")) or "Allergy"
    return _fact("allergy", f"Allergy: {allergy}", [allergy], citation, anchor)


def _problem_fact(
    fields: dict[str, Any],
    citation: CitationPayload,
    anchor: int,
    question: str,
) -> dict[str, Any]:
    del question
    problem = _coding_text(fields.get("code")) or "Problem"
    return _fact("problem", f"Problem: {problem}", [problem], citation, anchor)


def _demographics_fact(
    fields: dict[str, Any],
    citation: CitationPayload,
    anchor: int,
    question: str,
) -> dict[str, Any]:
    del question
    name = _patient_name(fields.get("name")) or "Patient"
    birth_date = _clean_string(fields.get("birthDate"))
    gender = _clean_string(fields.get("gender"))
    born = f"born {birth_date}" if birth_date else ""
    text = ", ".join(part for part in [name, gender, born] if part)
    return _fact("demographics", text, [name, gender, birth_date], citation, anchor)


def _encounter_fact(
    fields: dict[str, Any],
    citation: CitationPayload,
    anchor: int,
    question: str,
) -> dict[str, Any]:
    del question
    label = _first_coding_text(fields.get("type")) or "Encounter"
    return _fact("encounter", f"Encounter: {label}", [label], citation, anchor)


def _note_fact(
    fields: dict[str, Any],
    citation: CitationPayload,
    anchor: int,
    question: str,
) -> dict[str, Any]:
    description = _clean_string(fields.get("description")) or "Clinical note"
    date = _clean_string(fields.get("date"))
    text = f"{description} on {date}" if date else description
    fact_type = (
        "medication_change"
        if _contains_any(question.lower(), ("stopped", "discontinued", "changed"))
        else "note"
    )
    return _fact(
        fact_type,
        text,
        [description, date, _stringify(fields.get("content"))],
        citation,
        anchor,
    )


def _appointment_fact(
    fields: dict[str, Any],
    citation: CitationPayload,
    anchor: int,
    question: str,
) -> dict[str, Any]:
    del question
    description = _clean_string(fields.get("description")) or "Appointment"
    start = _clean_string(fields.get("start"))
    text = f"{description} on {start}" if start else description
    return _fact("appointment", text, [description, start], citation, anchor)


def _order_fact(
    fields: dict[str, Any],
    citation: CitationPayload,
    anchor: int,
    question: str,
) -> dict[str, Any]:
    del question
    order = _coding_text(fields.get("code")) or "Order"
    status = _clean_string(fields.get("status"))
    text = " ".join(part for part in [order, status] if part)
    return _fact("order", text, [order, status], citation, anchor)


def _procedure_fact(
    fields: dict[str, Any],
    citation: CitationPayload,
    anchor: int,
    question: str,
) -> dict[str, Any]:
    del question
    procedure = _coding_text(fields.get("code")) or "Procedure"
    performed = _clean_string(fields.get("performedDateTime"))
    text = f"{procedure} on {performed}" if performed else procedure
    return _fact("procedure", text, [procedure, performed], citation, anchor)


def _care_plan_fact(
    fields: dict[str, Any],
    citation: CitationPayload,
    anchor: int,
    question: str,
) -> dict[str, Any]:
    del question
    description = _coding_text(fields.get("description")) or _clean_string(
        fields.get("description")
    )
    if not description:
        description = _clean_string(fields.get("lifecycleStatus")) or "Care plan"
    return _fact("care_plan", description, [description], citation, anchor)


def _diagnostic_report_fact(
    fields: dict[str, Any],
    citation: CitationPayload,
    anchor: int,
    question: str,
) -> dict[str, Any]:
    del question
    report = _coding_text(fields.get("code")) or "Diagnostic report"
    return _fact("diagnostic_report", report, [report], citation, anchor)


def _questionnaire_response_fact(
    fields: dict[str, Any],
    citation: CitationPayload,
    anchor: int,
    question: str,
) -> dict[str, Any]:
    del question
    items = fields.get("item")
    label: str | None = None
    answer: str | None = None
    if isinstance(items, list):
        for entry in items:
            if not isinstance(entry, dict):
                continue
            label = _clean_string(entry.get("text")) or label
            answers = entry.get("answer")
            if isinstance(answers, list) and answers:
                first = answers[0]
                if isinstance(first, dict):
                    for key in (
                        "valueString",
                        "valueBoolean",
                        "valueInteger",
                        "valueDecimal",
                        "valueDate",
                    ):
                        if key in first:
                            answer = str(first[key])
                            break
            if label and answer:
                break
    text = ": ".join(part for part in [label or "Intake answer", answer] if part)
    return _fact("intake_answer", text, [label, answer], citation, anchor)


_MOCK_FACT_BUILDERS: dict[str, MockFactBuilder] = {
    "AllergyIntolerance": _allergy_fact,
    "Appointment": _appointment_fact,
    "CarePlan": _care_plan_fact,
    "Condition": _problem_fact,
    "DiagnosticReport": _diagnostic_report_fact,
    "DocumentReference": _note_fact,
    "Encounter": _encounter_fact,
    "Goal": _care_plan_fact,
    "Immunization": _immunization_fact,
    "MedicationRequest": _medication_fact,
    "Observation": _observation_fact,
    "Patient": _demographics_fact,
    "Procedure": _procedure_fact,
    "QuestionnaireResponse": _questionnaire_response_fact,
    "ServiceRequest": _order_fact,
}


def _fact(
    fact_type: str,
    text: str,
    excerpts: list[str | None],
    citations: list[dict[str, str]],
    anchor: int,
) -> dict[str, Any]:
    return {
        "type": fact_type,
        "text": text,
        "verbatim_excerpts": [excerpt for excerpt in excerpts if excerpt],
        "citations": citations,
        "anchor": anchor,
    }


def _coding_text(value: object) -> str | None:
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        coding = value.get("coding")
        return _first_coding_text(coding)
    if isinstance(value, list):
        return _first_coding_text(value)
    return None


def _first_coding_text(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        display = item.get("display")
        if isinstance(display, str) and display.strip():
            return display.strip()
        coding = item.get("coding")
        if isinstance(coding, list):
            nested = _first_coding_text(coding)
            if nested is not None:
                return nested
    return None


def _quantity_text(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    raw_quantity = value.get("value")
    if raw_quantity is None:
        return None
    unit = value.get("unit")
    quantity = str(raw_quantity)
    if isinstance(unit, str) and unit.strip():
        return f"{quantity} {unit.strip()}"
    return quantity


def _clean_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _stringify(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except TypeError:
        return str(value)


def _patient_name(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _is_lab_result(label: str, fields: dict[str, Any]) -> bool:
    category = fields.get("category")
    category_blob = json.dumps(category).lower() if category is not None else ""
    label_lower = label.lower()
    return (
        "laboratory" in category_blob
        or "lab" in category_blob
        or "a1c" in label_lower
        or "hemoglobin" in label_lower
    )


def _is_vital_sign(label: str, fields: dict[str, Any]) -> bool:
    category = fields.get("category")
    category_blob = json.dumps(category).lower() if category is not None else ""
    label_lower = label.lower()
    return (
        "vital-signs" in category_blob
        or "vital" in category_blob
        or "blood pressure" in label_lower
        or "heart rate" in label_lower
        or "body weight" in label_lower
        or "bmi" in label_lower
    )


if __name__ == "__main__":
    raise SystemExit(main())
