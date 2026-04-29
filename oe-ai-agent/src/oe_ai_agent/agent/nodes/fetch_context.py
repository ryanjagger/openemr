"""fetch_context node — runs the seven FHIR tools in parallel.

Phase 2 stub: this is a plain async function. Phase 3 wraps it as a
LangGraph node so verifier / llm_call / parse_output can chain after it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass

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
    """Run all tools in parallel; tolerate per-tool failures."""

    async def _run(name: str, tool: object) -> tuple[str, list[TypedRow] | ToolError]:
        try:
            assert callable(tool)
            coro: Awaitable[list[TypedRow]] = tool(client, patient_uuid)
            return name, await coro
        except FhirError as exc:
            return name, ToolError(
                tool_name=name,
                message=str(exc),
                status_code=exc.status_code,
            )
        except Exception as exc:
            return name, ToolError(tool_name=name, message=f"{type(exc).__name__}: {exc}")

    results = await asyncio.gather(*(_run(name, tool) for name, tool in _TOOL_REGISTRY))

    rows: list[TypedRow] = []
    errors: list[ToolError] = []
    for _, outcome in results:
        if isinstance(outcome, ToolError):
            errors.append(outcome)
        else:
            rows.extend(outcome)

    return FetchContextResult(rows=rows, errors=errors)
