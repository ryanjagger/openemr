"""supervisor node — routes between extractor, evidence_retriever, finalize.

LLM-driven router. No tools — emits structured JSON conforming to
``SupervisorRoute`` and we ``Command(goto=...)`` based on the chosen route.

Guardrails (applied BEFORE asking the LLM, since they are deterministic):

* If ``supervisor_turns_remaining`` hits zero → force ``finalize``.
* If extractor has already run twice → strip ``extractor`` from the choices.
* If evidence has already run three times → strip ``evidence_retriever``.
* If unindexed docs list is empty → strip ``extractor`` (the worker has
  nothing to do).

After the LLM returns, we honor its choice as long as it's still allowed.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from langgraph.types import Command

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.llm.client import LlmClient
from oe_ai_agent.llm.prompts_supervisor import (
    build_supervisor_messages,
    supervisor_response_format,
)
from oe_ai_agent.observability import current_trace, get_logger, step

logger = get_logger(__name__)

EXTRACTOR_RUNS_CAP = 2
EVIDENCE_RUNS_CAP = 3

SupervisorNode = Callable[[ChatState], Awaitable[Command[str]]]


def make_supervisor_node(llm: LlmClient) -> SupervisorNode:
    async def supervisor_node(state: ChatState) -> Command[str]:
        async with step("supervisor", model=llm.model_id) as record:
            allowed_routes = _allowed_routes(state)
            forced = _forced_route(state, allowed_routes)
            if forced is not None:
                record.attrs.update(
                    {
                        "next": forced,
                        "reason": "guardrail",
                        "forced": True,
                        "allowed_routes": list(allowed_routes),
                    }
                )
                return _command_for_route(state, forced)

            messages = build_supervisor_messages(
                patient_uuid=state.patient_uuid,
                history=state.history,
                cached_context=state.cached_context,
                unindexed_documents=state.unindexed_documents,
                supervisor_decisions=state.supervisor_decisions,
                extractor_runs=state.extractor_runs,
                evidence_runs=state.evidence_runs,
            )
            result = await llm.chat_with_tools(
                messages,
                tools=None,
                response_format=supervisor_response_format(),
            )
            collector = current_trace()
            if collector is not None:
                collector.add_usage(result.usage)

            chosen, reason = _parse_route(result.content, allowed_routes)
            record.attrs.update(
                {
                    "next": chosen,
                    "reason": reason[:200],
                    "forced": False,
                    "allowed_routes": list(allowed_routes),
                    "prompt_tokens": result.usage.prompt_tokens,
                    "completion_tokens": result.usage.completion_tokens,
                    "latency_ms": result.usage.latency_ms,
                }
            )
            return _command_for_route(state, chosen)

    return supervisor_node


def _allowed_routes(state: ChatState) -> tuple[str, ...]:
    routes: list[str] = ["finalize"]
    if state.evidence_runs < EVIDENCE_RUNS_CAP:
        routes.insert(0, "evidence_retriever")
    if state.unindexed_documents and state.extractor_runs < EXTRACTOR_RUNS_CAP:
        routes.insert(0, "extractor")
    return tuple(routes)


def _forced_route(state: ChatState, allowed_routes: tuple[str, ...]) -> str | None:
    if state.supervisor_turns_remaining <= 0:
        return "finalize"
    if allowed_routes == ("finalize",):
        return "finalize"
    return None


def _parse_route(content: str | None, allowed_routes: tuple[str, ...]) -> tuple[str, str]:
    if content:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            payload = {}
    else:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    next_value = payload.get("next")
    reason = payload.get("reason")
    if not isinstance(next_value, str) or next_value not in allowed_routes:
        next_value = allowed_routes[0]
        reason_text = "supervisor returned invalid route; falling back"
    else:
        reason_text = reason if isinstance(reason, str) and reason else ""
    return next_value, reason_text


def _command_for_route(state: ChatState, route: str) -> Command[str]:
    decisions = [*state.supervisor_decisions, route]
    return Command(
        goto=route,
        update={
            "supervisor_decisions": decisions,
            "supervisor_turns_remaining": max(0, state.supervisor_turns_remaining - 1),
        },
    )
