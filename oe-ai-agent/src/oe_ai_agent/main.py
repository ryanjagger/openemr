"""FastAPI app for the OpenEMR AI Agent sidecar."""

from __future__ import annotations

import logging
from functools import cache

from fastapi import Depends, FastAPI

from oe_ai_agent.agent.graph import build_graph
from oe_ai_agent.agent.state import AgentState
from oe_ai_agent.auth import require_internal_auth
from oe_ai_agent.config import load_settings
from oe_ai_agent.llm import LiteLLMClient, LlmClient, MockLlmClient
from oe_ai_agent.schemas import BriefRequest, BriefResponse
from oe_ai_agent.schemas.brief import VerificationFailure

logger = logging.getLogger(__name__)

app = FastAPI(title="oe-ai-agent", version="0.1.0")


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
        return LiteLLMClient(model=settings.llm_model, api_key=settings.anthropic_api_key)
    raise RuntimeError(f"Unknown LLM_PROVIDER: {provider!r}")


@cache
def _graph() -> object:
    return build_graph(_llm_client())


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
    try:
        final_state_dict = await _graph().ainvoke(initial)  # type: ignore[attr-defined]
    except Exception as exc:
        logger.exception(
            "agent graph failed",
            extra={"request_id": request.request_id},
        )
        return BriefResponse(
            request_id=request.request_id,
            items=[],
            verification_failures=[
                VerificationFailure(
                    rule="agent_error",
                    detail=_summarize_error(exc),
                ),
            ],
        )

    final = AgentState.model_validate(final_state_dict)

    return BriefResponse(
        request_id=request.request_id,
        items=final.verified_items,
        verification_failures=final.verification_failures,
    )


def _summarize_error(exc: BaseException) -> str:
    """Compact, user-safe error string for the panel.

    LiteLLM exception messages start with ``litellm.<ErrorType>: <provider>: <body>``
    and contain the upstream JSON. We keep the provider type + body but strip
    leading qualifiers so the panel shows something readable.
    """
    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    # Trim the duplicated "litellm.BadRequestError: " prefix LiteLLM adds.
    for prefix in ("litellm.", ):
        idx = text.find(prefix)
        if idx >= 0:
            text = text[idx + len(prefix) :]
            break
    return text[:400]
