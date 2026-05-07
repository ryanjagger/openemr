"""extractor worker — picks unindexed docs to extract and runs them.

Bounded tool-loop with the EXTRACTOR_TOOL_NAMES subset (list_unindexed_documents,
extract_documents). Block-extracts and merges resulting IndexedDocumentFact
rows into ``cached_context``. Returns ``Command(goto="supervisor")``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langgraph.types import Command

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.agent.nodes._tool_loop import merge_rows, run_tool_loop
from oe_ai_agent.llm.client import LlmClient
from oe_ai_agent.llm.prompts_supervisor import build_extractor_messages
from oe_ai_agent.observability import step
from oe_ai_agent.tools import FhirClient
from oe_ai_agent.tools.chat_registry import EXTRACTOR_TOOL_NAMES, extractor_tools_schema

ExtractorNode = Callable[[ChatState], Awaitable[Command[str]]]

DEFAULT_MAX_ITERATIONS = 3


def make_extractor_node(
    llm: LlmClient,
    *,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> ExtractorNode:
    async def extractor_node(state: ChatState) -> Command[str]:
        async with step("extractor", model=llm.model_id) as outer:
            last_user_message = _last_user_message(state) or ""
            async with FhirClient(
                base_url=state.fhir_base_url,
                bearer_token=state.bearer_token.get_secret_value(),
                request_id=state.request_id,
            ) as client:
                messages = build_extractor_messages(
                    patient_uuid=state.patient_uuid,
                    last_user_message=last_user_message,
                    unindexed_documents=state.unindexed_documents,
                    cached_context=state.cached_context,
                )
                rows, errors, iterations, tool_calls, _ = await run_tool_loop(
                    llm=llm,
                    client=client,
                    patient_uuid=state.patient_uuid,
                    messages=messages,
                    tools=extractor_tools_schema(),
                    response_format=None,
                    allowed_tool_names=EXTRACTOR_TOOL_NAMES,
                    max_iterations=max_iterations,
                    loop_label="extractor",
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
                "extractor_runs": state.extractor_runs + 1,
            },
        )

    return extractor_node


def _last_user_message(state: ChatState) -> str | None:
    for msg in reversed(state.history):
        if msg.role.value == "user":
            return msg.content
    return None
