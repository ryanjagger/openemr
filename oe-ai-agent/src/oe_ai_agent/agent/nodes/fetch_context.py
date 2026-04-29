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

from oe_ai_agent.observability import StepRecord, current_trace
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
        try:
            assert callable(tool)
            coro: Awaitable[list[TypedRow]] = tool(client, patient_uuid)
            rows = await coro
            duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
            return name, rows, duration_ms, started
        except FhirError as exc:
            duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
            return (
                name,
                ToolError(tool_name=name, message=str(exc), status_code=exc.status_code),
                duration_ms,
                started,
            )
        except Exception as exc:
            duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
            return (
                name,
                ToolError(tool_name=name, message=f"{type(exc).__name__}: {exc}"),
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
