"""evidence_retriever worker — calls chart, indexed-doc, and guideline tools.

Bounded tool-loop with the EVIDENCE_TOOL_NAMES subset of the chat tool
registry (everything except the extractor tools). Mirrors the historical
single-node ``llm_turn`` behavior, but is now scoped to the evidence
sub-task by the supervisor and may run multiple times per chat turn.

Returns ``Command(goto="supervisor")`` with merged rows.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langgraph.types import Command

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.agent.nodes._tool_loop import merge_rows, run_tool_loop
from oe_ai_agent.llm.client import LlmClient
from oe_ai_agent.llm.prompts_supervisor import build_evidence_messages
from oe_ai_agent.observability import step
from oe_ai_agent.schemas.chat import ChatFactType
from oe_ai_agent.tools import FhirClient
from oe_ai_agent.tools.chat_registry import EVIDENCE_TOOL_NAMES, evidence_tools_schema

EvidenceNode = Callable[[ChatState], Awaitable[Command[str]]]

DEFAULT_MAX_ITERATIONS = 4


def make_evidence_retriever_node(
    llm: LlmClient,
    *,
    allowed_types: frozenset[ChatFactType] | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> EvidenceNode:
    types = allowed_types if allowed_types is not None else frozenset(ChatFactType)

    async def evidence_node(state: ChatState) -> Command[str]:
        async with step("evidence_retriever", model=llm.model_id) as outer:
            async with FhirClient(
                base_url=state.fhir_base_url,
                bearer_token=state.bearer_token.get_secret_value(),
                request_id=state.request_id,
            ) as client:
                messages = build_evidence_messages(
                    patient_uuid=state.patient_uuid,
                    history=state.history,
                    cached_context=state.cached_context,
                    allowed_types=types,
                )
                rows, errors, iterations, tool_calls, _ = await run_tool_loop(
                    llm=llm,
                    client=client,
                    patient_uuid=state.patient_uuid,
                    messages=messages,
                    tools=evidence_tools_schema(),
                    response_format=None,
                    allowed_tool_names=EVIDENCE_TOOL_NAMES,
                    max_iterations=max_iterations,
                    loop_label="evidence_retriever",
                )

            outer.attrs.update(
                {
                    "iterations": iterations,
                    "tool_calls": tool_calls,
                    "new_row_count": len(rows),
                    "error_count": len(errors),
                }
            )

        merged_context = merge_rows(state.cached_context, rows)
        return Command(
            goto="supervisor",
            update={
                "cached_context": merged_context,
                "fetch_errors": [*state.fetch_errors, *errors],
                "evidence_runs": state.evidence_runs + 1,
            },
        )

    return evidence_node
