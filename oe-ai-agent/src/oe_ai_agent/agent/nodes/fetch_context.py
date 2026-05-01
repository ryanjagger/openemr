"""fetch_context node — runs the seven FHIR tools in parallel.

Phase 2 stub: this is a plain async function. Phase 3 wraps it as a
LangGraph node so verifier / llm_call / parse_output can chain after it.
Tool-level success and failure are emitted as individual ``tool.fhir``
trace steps so the admin viewer can show which tool failed.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from dataclasses import dataclass

from oe_ai_agent.observability import StepRecord, current_trace, langfuse_observation
from oe_ai_agent.schemas.tool_results import ToolError, TypedRow
from oe_ai_agent.tools import (
    FhirClient,
    FhirError,
    get_active_medications,
    get_active_problems,
    get_allergies,
    get_demographics,
    get_recent_encounters,
    get_recent_notes,
    get_recent_observations,
)


@dataclass(frozen=True)
class FetchContextResult:
    rows: list[TypedRow]
    errors: list[ToolError]


_TOOL_REGISTRY: tuple[tuple[str, object], ...] = (
    ("get_demographics", get_demographics),
    ("get_active_problems", get_active_problems),
    ("get_active_medications", get_active_medications),
    ("get_allergies", get_allergies),
    ("get_recent_encounters", get_recent_encounters),
    ("get_recent_observations", get_recent_observations),
    ("get_recent_notes", get_recent_notes),
)


async def fetch_context(client: FhirClient, patient_uuid: str) -> FetchContextResult:
    """Run all tools in parallel; tolerate per-tool failures.

    Each tool produces one trace step (status='ok' with row count, or
    status='error' with the exception summary). Tools run concurrently so
    we can't use the ``step()`` context manager — we record them
    explicitly into the active TraceCollector.
    """

    async def _run(
        name: str, tool: object
    ) -> tuple[str, list[TypedRow] | ToolError, int, int]:
        started = time.monotonic_ns()
        async with langfuse_observation(
            name=f"tool.{name}",
            as_type="tool",
            input_payload={"tool": name, "patient_uuid": patient_uuid},
        ) as tool_span:
            try:
                assert callable(tool)
                coro: Awaitable[list[TypedRow]] = tool(client, patient_uuid)
                rows = await coro
                duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
                tool_span.update(
                    output=_rows_payload(rows),
                    metadata={"status": "ok", "row_count": len(rows)},
                )
                return name, rows, duration_ms, started
            except FhirError as exc:
                duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
                tool_span.update(
                    output={"error": str(exc), "status_code": exc.status_code},
                    metadata={"status": "error", "status_code": exc.status_code},
                )
                return (
                    name,
                    ToolError(tool_name=name, message=str(exc), status_code=exc.status_code),
                    duration_ms,
                    started,
                )
            except Exception as exc:
                duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
                message = f"{type(exc).__name__}: {exc}"
                tool_span.update(output={"error": message}, metadata={"status": "error"})
                return (
                    name,
                    ToolError(tool_name=name, message=message),
                    duration_ms,
                    started,
                )

    results = await asyncio.gather(*(_run(name, tool) for name, tool in _TOOL_REGISTRY))

    rows: list[TypedRow] = []
    errors: list[ToolError] = []
    collector = current_trace()
    for name, outcome, duration_ms, started_ns in results:
        if isinstance(outcome, ToolError):
            errors.append(outcome)
            if collector is not None:
                collector.add(
                    StepRecord(
                        name=f"tool.{name}",
                        duration_ms=duration_ms,
                        status="error",
                        error=outcome.message[:400],
                        attrs={"status_code": outcome.status_code or 0},
                        started_at_monotonic_ns=started_ns,
                    )
                )
        else:
            rows.extend(outcome)
            if collector is not None:
                collector.add(
                    StepRecord(
                        name=f"tool.{name}",
                        duration_ms=duration_ms,
                        status="ok",
                        attrs={"row_count": len(outcome)},
                        started_at_monotonic_ns=started_ns,
                    )
                )

    return FetchContextResult(rows=rows, errors=errors)


def _rows_payload(rows: list[TypedRow]) -> list[dict[str, object]]:
    return [row.model_dump(mode="json") for row in rows]
