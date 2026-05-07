"""Shared tool-loop primitives for the extractor + evidence_retriever workers.

Each worker drives a small bounded loop of LLM-with-tools calls. The shape
is identical (model proposes tool calls → we execute → append results →
ask the model again, until it returns plain content or we hit the iter
cap). Only the prompt, schema, and tool subset differ.

This module keeps the loop, message accounting, and trace emission in one
place so the two worker nodes stay readable.
"""

from __future__ import annotations

import json
from typing import Any

from oe_ai_agent.llm.client import LlmChatResult, LlmClient, LlmToolCall
from oe_ai_agent.observability import (
    current_trace,
    get_logger,
    step,
    update_langfuse_observation,
)
from oe_ai_agent.schemas.tool_results import ToolError, TypedRow
from oe_ai_agent.tools import FhirClient
from oe_ai_agent.tools.chat_registry import execute_chat_tool

logger = get_logger(__name__)

_TOOL_ARGS_MAX = 200


async def run_tool_loop(
    *,
    llm: LlmClient,
    client: FhirClient,
    patient_uuid: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, object]],
    response_format: dict[str, Any] | None,
    allowed_tool_names: frozenset[str],
    max_iterations: int,
    loop_label: str,
) -> tuple[list[TypedRow], list[ToolError], int, int, str | None]:
    """Run a bounded tool-loop. Returns (rows, errors, iterations, tool_calls, content).

    ``content`` is the final assistant message string when the loop ends
    naturally (no tool calls). It's ``None`` if we exhausted the iteration
    cap without the model finishing.
    """
    new_rows: list[TypedRow] = []
    new_errors: list[ToolError] = []
    iterations = 0
    tool_call_count = 0
    final_content: str | None = None

    for _ in range(max_iterations):
        iterations += 1
        async with step(f"{loop_label}.iteration", iteration=iterations) as iter_record:
            result = await llm.chat_with_tools(
                messages,
                tools=tools,
                response_format=response_format,
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
            final_content = result.content
            break

        messages.append(_assistant_message_with_tool_calls(result))
        for call in result.tool_calls:
            tool_call_count += 1
            if call.name not in allowed_tool_names:
                error_message = (
                    f"tool {call.name!r} not allowed for {loop_label}; "
                    f"allowed: {sorted(allowed_tool_names)}"
                )
                new_errors.append(ToolError(tool_name=call.name, message=error_message))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.tool_call_id,
                        "name": call.name,
                        "content": json.dumps({"error": error_message}),
                    }
                )
                continue

            rows, error, payload = await _execute_traced_tool(
                client,
                patient_uuid,
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
            "%s loop hit max_iterations without final content",
            loop_label,
            max_iterations=max_iterations,
        )

    return new_rows, new_errors, iterations, tool_call_count, final_content


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


def _assistant_message_with_tool_calls(result: LlmChatResult) -> dict[str, object]:
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


def _sanitize_args(args: dict[str, object]) -> str:
    try:
        text = json.dumps(args, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return "<unserializable>"
    return text if len(text) <= _TOOL_ARGS_MAX else text[:_TOOL_ARGS_MAX] + "…"


def merge_rows(existing: list[TypedRow], incoming: list[TypedRow]) -> list[TypedRow]:
    seen = {(row.resource_type, row.resource_id) for row in existing}
    merged = list(existing)
    for row in incoming:
        key = (row.resource_type, row.resource_id)
        if key in seen:
            continue
        merged.append(row)
        seen.add(key)
    return merged
