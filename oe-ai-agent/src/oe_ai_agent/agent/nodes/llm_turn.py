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
from datetime import date

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.llm.client import LlmChatResult, LlmClient, LlmToolCall
from oe_ai_agent.llm.prompts_chat import (
    build_chat_messages,
    chat_response_format,
    chat_tools_schema,
)
from oe_ai_agent.observability import current_trace, get_logger, step
from oe_ai_agent.schemas.brief import BriefItemType
from oe_ai_agent.schemas.tool_results import ToolError, TypedRow
from oe_ai_agent.tools import FhirClient, FhirError, get_lab_trend

logger = get_logger(__name__)

LlmTurnNode = Callable[[ChatState], Awaitable[dict[str, object]]]

DEFAULT_MAX_ITERATIONS = 5


def make_llm_turn_node(
    llm: LlmClient,
    *,
    allowed_types: frozenset[BriefItemType] | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> LlmTurnNode:
    types = allowed_types if allowed_types is not None else frozenset(BriefItemType)

    async def llm_turn_node(state: ChatState) -> dict[str, object]:
        async with step("llm_turn", model=llm.model_id) as outer:
            messages = build_chat_messages(
                patient_uuid=state.patient_uuid,
                cached_context=state.cached_context,
                history=state.history,
                allowed_types=types,
            )
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
                        async with step(
                            "tool_call", tool=call.name, args=_sanitize_args(call.arguments)
                        ) as tool_record:
                            rows, error, payload = await _execute_tool_call(
                                call, client, state.patient_uuid
                            )
                            if error is not None:
                                new_errors.append(error)
                                tool_record.status = "error"
                                tool_record.error = error.message[:400]
                                tool_record.attrs["status_code"] = error.status_code or 0
                            else:
                                tool_record.attrs["row_count"] = len(rows)
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


async def _execute_tool_call(
    call: LlmToolCall,
    client: FhirClient,
    patient_uuid: str,
) -> tuple[list[TypedRow], ToolError | None, dict[str, object]]:
    if call.name != "get_lab_trend":
        return (
            [],
            ToolError(tool_name=call.name, message=f"unknown tool {call.name!r}"),
            {"error": f"unknown tool {call.name!r}"},
        )
    code_or_text = call.arguments.get("code_or_text")
    if not isinstance(code_or_text, str) or not code_or_text.strip():
        return (
            [],
            ToolError(tool_name=call.name, message="missing code_or_text"),
            {"error": "missing required argument code_or_text"},
        )
    since_raw = call.arguments.get("since")
    since: date | None = None
    if isinstance(since_raw, str) and since_raw.strip():
        try:
            since = date.fromisoformat(since_raw)
        except ValueError:
            return (
                [],
                ToolError(tool_name=call.name, message=f"bad since {since_raw!r}"),
                {"error": f"invalid since {since_raw!r}; expected YYYY-MM-DD"},
            )
    try:
        rows = await get_lab_trend(
            client,
            patient_uuid,
            code_or_text=code_or_text.strip(),
            since=since,
        )
    except FhirError as exc:
        return (
            [],
            ToolError(
                tool_name=call.name,
                message=str(exc),
                status_code=exc.status_code,
            ),
            {"error": f"FHIR error: {exc}"},
        )
    return (
        rows,
        None,
        {
            "rows": [
                {
                    "resource_type": r.resource_type,
                    "resource_id": r.resource_id,
                    "last_updated": r.last_updated.isoformat(),
                    "fields": r.fields,
                }
                for r in rows
            ],
        },
    )


def _merge_rows(existing: list[TypedRow], incoming: list[TypedRow]) -> list[TypedRow]:
    seen = {row.resource_id for row in existing}
    merged = list(existing)
    for row in incoming:
        if row.resource_id in seen:
            continue
        merged.append(row)
        seen.add(row.resource_id)
    return merged
