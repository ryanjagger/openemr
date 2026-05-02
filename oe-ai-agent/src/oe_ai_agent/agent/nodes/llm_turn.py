"""llm_turn node — runs the LLM with bounded tool-calling loop.

Single node rather than a LangGraph sub-loop: tool-calling is tightly
coupled to the model call, max iterations are small, and a flat node keeps
the chat graph linear (``ensure_context → llm_turn → parse_envelope →
verify_chat``).

Trace events emitted (one per loop iteration):

* ``llm_turn.iteration`` — one per LLM call, with token counts.
* ``tool_call`` — one per tool invocation, with tool name + status.

The outer ``llm_turn`` step records the iteration count and total tokens.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.llm.client import LlmChatResult, LlmClient, LlmToolCall
from oe_ai_agent.llm.prompts_chat import (
    build_chat_messages,
    chat_response_format,
)
from oe_ai_agent.observability import (
    current_trace,
    get_logger,
    step,
    update_langfuse_observation,
)
from oe_ai_agent.schemas.chat import ChatFactType
from oe_ai_agent.schemas.tool_results import ToolError, TypedRow
from oe_ai_agent.tools import FhirClient
from oe_ai_agent.tools.chat_registry import chat_tools_schema, execute_chat_tool

logger = get_logger(__name__)

LlmTurnNode = Callable[[ChatState], Awaitable[dict[str, object]]]

DEFAULT_MAX_ITERATIONS = 5


def make_llm_turn_node(
    llm: LlmClient,
    *,
    allowed_types: frozenset[ChatFactType] | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> LlmTurnNode:
    types = allowed_types if allowed_types is not None else frozenset(ChatFactType)

    async def llm_turn_node(state: ChatState) -> dict[str, object]:
        async with step("llm_turn", model=llm.model_id) as outer:
            new_rows: list[TypedRow] = []
            new_errors: list[ToolError] = []
            iterations = 0
            tool_call_count = 0

            envelope: str | None = None
            async with FhirClient(
                base_url=state.fhir_base_url,
                bearer_token=state.bearer_token.get_secret_value(),
                request_id=state.request_id,
            ) as client:
                messages = build_chat_messages(
                    patient_uuid=state.patient_uuid,
                    cached_context=state.cached_context,
                    history=state.history,
                    allowed_types=types,
                )
                for _ in range(max_iterations):
                    iterations += 1
                    async with step(
                        "llm_turn.iteration", iteration=iterations
                    ) as iter_record:
                        result = await llm.chat_with_tools(
                            messages,
                            tools=chat_tools_schema(),
                            response_format=chat_response_format(types),
                        )
                        iter_record.attrs.update(
                            {
                                "prompt_tokens": result.usage.prompt_tokens,
                                "completion_tokens": result.usage.completion_tokens,
                                "latency_ms": result.usage.latency_ms,
                                "tool_call_count": len(result.tool_calls),
                            }
                        )
                        collector = current_trace()
                        if collector is not None:
                            collector.add_usage(result.usage)
                    if not result.tool_calls:
                        envelope = result.content
                        break
                    messages.append(_assistant_message_with_tool_calls(result))
                    for call in result.tool_calls:
                        tool_call_count += 1
                        rows, error, payload = await _execute_traced_tool(
                            client,
                            state.patient_uuid,
                            name=call.name,
                            arguments=call.arguments,
                        )
                        if error is not None:
                            new_errors.append(error)
                        new_rows.extend(rows)
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.tool_call_id,
                                "name": call.name,
                                "content": json.dumps(payload),
                            }
                        )
                else:
                    logger.warning(
                        "chat tool loop hit max_iterations without final envelope",
                        max_iterations=max_iterations,
                    )

            outer.attrs.update(
                {"iterations": iterations, "tool_calls": tool_call_count}
            )

        merged_context = _merge_rows(state.cached_context, new_rows)
        return {
            "cached_context": merged_context,
            "fetch_errors": [*state.fetch_errors, *new_errors],
            "raw_envelope": envelope,
        }

    return llm_turn_node


async def _execute_traced_tool(
    client: FhirClient,
    patient_uuid: str,
    *,
    name: str,
    arguments: dict[str, object],
    source: str = "model",
) -> tuple[list[TypedRow], ToolError | None, dict[str, object]]:
    async with step(
        "tool_call",
        tool=name,
        args=_sanitize_args(arguments),
        source=source,
    ) as tool_record:
        rows, error, payload = await execute_chat_tool(
            LlmToolCall(
                tool_call_id="deterministic",
                name=name,
                arguments=arguments,
            ),
            client,
            patient_uuid,
        )
        if error is not None:
            tool_record.status = "error"
            tool_record.error = error.message[:400]
            tool_record.attrs["status_code"] = error.status_code or 0
            update_langfuse_observation(
                output=payload,
                metadata={
                    "status": "error",
                    "status_code": error.status_code,
                    "row_count": 0,
                },
            )
        else:
            tool_record.attrs["row_count"] = len(rows)
            update_langfuse_observation(
                output=payload,
                metadata={"status": "ok", "row_count": len(rows)},
            )
        return rows, error, payload


def _assistant_message_with_tool_calls(
    result: LlmChatResult,
) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": result.content or "",
        "tool_calls": [
            {
                "id": call.tool_call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in result.tool_calls
        ],
    }


_TOOL_ARGS_MAX = 200


def _sanitize_args(args: dict[str, object]) -> str:
    """Compact JSON of args, truncated. Avoids dumping large blobs to logs."""
    try:
        text = json.dumps(args, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return "<unserializable>"
    return text if len(text) <= _TOOL_ARGS_MAX else text[:_TOOL_ARGS_MAX] + "…"


def _merge_rows(existing: list[TypedRow], incoming: list[TypedRow]) -> list[TypedRow]:
    seen = {(row.resource_type, row.resource_id) for row in existing}
    merged = list(existing)
    for row in incoming:
        key = (row.resource_type, row.resource_id)
        if key in seen:
            continue
        merged.append(row)
        seen.add(key)
    return merged
