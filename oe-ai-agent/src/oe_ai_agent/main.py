"""FastAPI app for the OpenEMR AI Agent sidecar."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import cache

from fastapi import Depends, FastAPI

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.agent.graph import build_graph
from oe_ai_agent.agent.graph_chat import build_chat_graph
from oe_ai_agent.agent.state import AgentState
from oe_ai_agent.auth import require_internal_auth
from oe_ai_agent.config import load_settings
from oe_ai_agent.conversation import TurnLimitError, get_default_store
from oe_ai_agent.llm import LiteLLMClient, LlmClient, MockLlmClient
from oe_ai_agent.observability import (
    bind_request_context,
    configure_logging,
    get_logger,
    langfuse_request_trace,
    shutdown_langfuse,
    use_trace,
)
from oe_ai_agent.observability.trace import TraceCollector, clear_request_context
from oe_ai_agent.schemas import (
    BriefRequest,
    BriefResponse,
    ChatRequest,
    ChatTurnResponse,
)
from oe_ai_agent.schemas.brief import VerificationFailure
from oe_ai_agent.schemas.observability import ResponseMeta, StepEntry, UsageBlock

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        shutdown_langfuse()


app = FastAPI(title="oe-ai-agent", version="0.1.0", lifespan=lifespan)


@cache
def _llm_client() -> LlmClient:
    settings = load_settings()
    provider = settings.llm_provider
    if provider == "mock":
        return MockLlmClient.synthesizing()
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY in the environment.",
            )
        return LiteLLMClient(
            model=settings.llm_model,
            api_key=settings.anthropic_api_key,
            max_tokens=settings.llm_max_tokens,
        )
    raise RuntimeError(f"Unknown LLM_PROVIDER: {provider!r}")


@cache
def _graph() -> object:
    settings = load_settings()
    return build_graph(_llm_client(), allowed_types=settings.allowed_item_types)


@cache
def _chat_graph() -> object:
    settings = load_settings()
    return build_chat_graph(_llm_client(), allowed_types=settings.allowed_chat_fact_types)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/brief", dependencies=[Depends(require_internal_auth)])
async def brief(request: BriefRequest) -> BriefResponse:
    initial = AgentState(
        patient_uuid=request.patient_uuid,
        fhir_base_url=request.fhir_base_url,
        bearer_token=request.bearer_token,
        request_id=request.request_id,
    )
    model_id = _llm_client().model_id

    bind_request_context(
        request_id=request.request_id,
        action="brief.read",
        patient_uuid=request.patient_uuid,
        model=model_id,
        user_id=request.user_id,
        session_id=request.session_id,
    )
    try:
        async with use_trace() as trace, langfuse_request_trace(
            name="brief.read",
            request_id=request.request_id,
            patient_uuid=request.patient_uuid,
            model_id=model_id,
            action="brief.read",
            input_payload={
                "patient_uuid": request.patient_uuid,
                "fhir_base_url": request.fhir_base_url,
            },
            user_id=request.user_id,
            session_id=request.session_id,
            tags=["brief", "demo"],
        ) as lf_trace:
            try:
                final_state_dict = await _graph().ainvoke(initial)  # type: ignore[attr-defined]
            except Exception as exc:
                logger.exception("agent graph failed")
                meta = _build_meta(trace)
                _emit_complete(
                    action="brief.read",
                    status="error",
                    error_code="agent_error",
                    meta=meta,
                )
                response = BriefResponse(
                    request_id=request.request_id,
                    model_id=model_id,
                    items=[],
                    verification_failures=[
                        VerificationFailure(
                            rule="agent_error",
                            detail=_summarize_error(exc),
                        ),
                    ],
                    meta=meta,
                )
                lf_trace.update(
                    output=response.model_dump_json_safe(),
                    metadata={"status": "error", "error_code": "agent_error"},
                )
                return response

            final = AgentState.model_validate(final_state_dict)
            meta = _build_meta(trace)
            status = "ok" if not final.verification_failures else "partial"
            _emit_complete(
                action="brief.read",
                status=status,
                error_code=None,
                meta=meta,
            )
            response = BriefResponse(
                request_id=request.request_id,
                model_id=model_id,
                items=final.verified_items,
                verification_failures=final.verification_failures,
                meta=meta,
            )
            lf_trace.update(
                output=response.model_dump_json_safe(),
                metadata={
                    "status": status,
                    "verified_item_count": len(final.verified_items),
                    "verification_failure_count": len(final.verification_failures),
                },
            )
            return response
    finally:
        clear_request_context()


@app.post("/v1/chat", dependencies=[Depends(require_internal_auth)])
async def chat(request: ChatRequest) -> ChatTurnResponse:
    model_id = _llm_client().model_id
    store = get_default_store()
    entry = await store.get_or_create(request.conversation_id, request.patient_uuid)

    bind_request_context(
        request_id=request.request_id,
        conversation_id=entry.conversation_id,
        action="chat.turn",
        patient_uuid=request.patient_uuid,
        model=model_id,
        user_id=request.user_id,
        session_id=request.session_id,
    )
    try:
        async with use_trace() as trace, langfuse_request_trace(
            name="chat.turn",
            request_id=request.request_id,
            conversation_id=entry.conversation_id,
            patient_uuid=request.patient_uuid,
            model_id=model_id,
            action="chat.turn",
            input_payload={
                "patient_uuid": request.patient_uuid,
                "conversation_id": entry.conversation_id,
                "messages": [message.model_dump(mode="json") for message in request.messages],
            },
            user_id=request.user_id,
            session_id=request.session_id,
            tags=["chat", "demo"],
        ) as lf_trace:
            try:
                await store.increment_turn(entry.conversation_id)
            except TurnLimitError as exc:
                meta = _build_meta(trace)
                _emit_complete(
                    action="chat.turn",
                    status="denied",
                    error_code="turn_limit_exceeded",
                    meta=meta,
                )
                response = ChatTurnResponse(
                    request_id=request.request_id,
                    conversation_id=entry.conversation_id,
                    model_id=model_id,
                    narrative="",
                    facts=[],
                    verification_failures=[
                        VerificationFailure(
                            rule="turn_limit_exceeded", detail=str(exc)
                        ),
                    ],
                    meta=meta,
                )
                lf_trace.update(
                    output=response.model_dump_json_safe(),
                    metadata={"status": "denied", "error_code": "turn_limit_exceeded"},
                )
                return response

            initial = ChatState(
                patient_uuid=request.patient_uuid,
                fhir_base_url=request.fhir_base_url,
                bearer_token=request.bearer_token,
                request_id=request.request_id,
                conversation_id=entry.conversation_id,
                history=list(request.messages),
                cached_context=list(entry.cached_context),
            )
            try:
                final_state_dict = await _chat_graph().ainvoke(initial)  # type: ignore[attr-defined]
            except Exception as exc:
                logger.exception("chat graph failed")
                meta = _build_meta(trace)
                _emit_complete(
                    action="chat.turn",
                    status="error",
                    error_code="agent_error",
                    meta=meta,
                )
                response = ChatTurnResponse(
                    request_id=request.request_id,
                    conversation_id=entry.conversation_id,
                    model_id=model_id,
                    narrative="",
                    facts=[],
                    verification_failures=[
                        VerificationFailure(
                            rule="agent_error", detail=_summarize_error(exc)
                        ),
                    ],
                    meta=meta,
                )
                lf_trace.update(
                    output=response.model_dump_json_safe(),
                    metadata={"status": "error", "error_code": "agent_error"},
                )
                return response

            final = ChatState.model_validate(final_state_dict)
            await store.update_context(entry.conversation_id, final.cached_context)
            meta = _build_meta(trace)
            status, error_code = _classify_chat_outcome(final)
            _emit_complete(
                action="chat.turn",
                status=status,
                error_code=error_code,
                meta=meta,
            )
            response = ChatTurnResponse(
                request_id=request.request_id,
                conversation_id=entry.conversation_id,
                model_id=model_id,
                narrative=final.parsed_narrative,
                facts=final.verified_facts,
                verification_failures=final.verification_failures,
                meta=meta,
            )
            lf_trace.update(
                output=response.model_dump_json_safe(),
                metadata={
                    "status": status,
                    "error_code": error_code,
                    "verified_fact_count": len(final.verified_facts),
                    "verification_failure_count": len(final.verification_failures),
                },
            )
            return response
    finally:
        clear_request_context()


def _build_meta(trace: TraceCollector) -> ResponseMeta:
    """Assemble the ResponseMeta from the active TraceCollector."""
    usage_summary = trace.usage_summary()
    return ResponseMeta(
        usage=UsageBlock(
            prompt_tokens=usage_summary["prompt_tokens"],
            completion_tokens=usage_summary["completion_tokens"],
            total_tokens=usage_summary["total_tokens"],
            cost_usd=usage_summary["cost_usd"],
            latency_ms_total=trace.total_duration_ms(),
        ),
        steps=[
            StepEntry(
                name=raw["name"],
                duration_ms=raw["duration_ms"],
                status=raw["status"] if raw["status"] in ("ok", "error") else "ok",
                error=raw["error"],
                attrs=dict(raw["attrs"]),
            )
            for raw in trace.to_list()
        ],
    )


def _emit_complete(
    *,
    action: str,
    status: str,
    error_code: str | None,
    meta: ResponseMeta,
) -> None:
    """One ``agent.request.complete`` log line per request — the 'grep me' line.

    Carries everything needed to answer the four observability questions
    in one place: action, status, error, total_ms, tokens, cost, step count.
    """
    logger.info(
        "agent.request.complete",
        status=status,
        error_code=error_code,
        total_ms=meta.usage.latency_ms_total,
        prompt_tokens=meta.usage.prompt_tokens,
        completion_tokens=meta.usage.completion_tokens,
        total_tokens=meta.usage.total_tokens,
        cost_usd=round(meta.usage.cost_usd, 6),
        step_count=len(meta.steps),
        steps=[s.name for s in meta.steps],
    )


def _classify_chat_outcome(final: ChatState) -> tuple[str, str | None]:
    """Map verification failures to (status, error_code) for logging."""
    if not final.verification_failures:
        return "ok", None
    rules = [f.rule for f in final.verification_failures]
    if any(rule.startswith("narrative_") or rule == "advisory_denylist" for rule in rules):
        return "denied", rules[0]
    return "partial", rules[0]


def _summarize_error(exc: BaseException) -> str:
    """Compact, user-safe error string for the panel.

    LiteLLM exception messages start with ``litellm.<ErrorType>: <provider>: <body>``
    and contain the upstream JSON. We keep the provider type + body but strip
    leading qualifiers so the panel shows something readable.
    """
    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    for prefix in ("litellm.",):
        idx = text.find(prefix)
        if idx >= 0:
            text = text[idx + len(prefix) :]
            break
    return text[:400]
