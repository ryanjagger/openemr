"""Per-request trace collector + ``step()`` async context manager.

The trace is the answer to the four observability questions:
- "What did the agent do?" → ``TraceCollector.records[*].name`` in order.
- "How long did each step take?" → ``StepRecord.duration_ms``.
- "Did any tool fail?" → ``StepRecord.status == 'error'`` plus
  ``StepRecord.error``.
- "How many tokens / cost?" → aggregated separately on the LlmUsage rollup
  carried into the response envelope, but step-level token counts (when
  available) are stored as ``attrs``.

Usage:

    async with use_trace() as trace:
        async with step("fetch_context"):
            ...
        async with step("llm_call", model=model_id):
            ...

The collector is bound to a contextvar so node functions don't have to
thread it manually. Nested ``step()`` blocks are supported (a tool call
inside ``llm_turn`` produces its own record but does not nest under
``llm_turn`` — the records are flat by design, since the admin timeline
renders better that way).
"""

from __future__ import annotations

import contextvars
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from oe_ai_agent.llm.client import LlmUsage

_TRACE_VAR: contextvars.ContextVar[TraceCollector | None] = contextvars.ContextVar(
    "oe_ai_agent_trace", default=None
)


@dataclass
class StepRecord:
    """One step in the agent's run. Serialized into the response meta."""

    name: str
    duration_ms: int = 0
    status: str = "ok"  # 'ok' | 'error'
    error: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    started_at_monotonic_ns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error": self.error,
            "attrs": dict(self.attrs),
        }


@dataclass
class TraceCollector:
    """Accumulates StepRecords + LLM usage rollup for a single request."""

    records: list[StepRecord] = field(default_factory=list)
    started_at_monotonic_ns: int = field(default_factory=time.monotonic_ns)
    _usage_prompt_tokens: int = 0
    _usage_completion_tokens: int = 0
    _usage_total_tokens: int = 0
    _usage_cost_usd: float = 0.0

    def add(self, record: StepRecord) -> None:
        self.records.append(record)

    def add_usage(self, usage: LlmUsage) -> None:
        """Sum a LlmUsage into the running total. Safe across tool-loop iterations."""
        self._usage_prompt_tokens += usage.prompt_tokens
        self._usage_completion_tokens += usage.completion_tokens
        self._usage_total_tokens += usage.total_tokens
        self._usage_cost_usd += usage.cost_usd

    def usage_summary(self) -> dict[str, Any]:
        """Return the accumulated usage as a plain dict (envelope-friendly)."""
        return {
            "prompt_tokens": self._usage_prompt_tokens,
            "completion_tokens": self._usage_completion_tokens,
            "total_tokens": self._usage_total_tokens
            or (self._usage_prompt_tokens + self._usage_completion_tokens),
            "cost_usd": self._usage_cost_usd,
        }

    def total_duration_ms(self) -> int:
        return max(0, (time.monotonic_ns() - self.started_at_monotonic_ns) // 1_000_000)

    def to_list(self) -> list[dict[str, Any]]:
        """Return records sorted by start time, not completion time.

        Steps land in ``self.records`` when they *finish*, so a long parent
        node (with quick child steps) lists its children before itself.
        For the admin timeline we want chronological start order, which
        matches how the run actually unfolded.
        """
        ordered = sorted(self.records, key=lambda r: r.started_at_monotonic_ns)
        return [r.to_dict() for r in ordered]


@asynccontextmanager
async def use_trace() -> AsyncIterator[TraceCollector]:
    """Bind a fresh collector to the contextvar for the duration of a request.

    Resetting on exit lets nested test invocations share a process safely.
    """
    collector = TraceCollector()
    token = _TRACE_VAR.set(collector)
    try:
        yield collector
    finally:
        _TRACE_VAR.reset(token)


def current_trace() -> TraceCollector | None:
    """Returns the active collector if a request is in progress, else None."""
    return _TRACE_VAR.get()


@asynccontextmanager
async def step(name: str, **attrs: Any) -> AsyncIterator[StepRecord]:
    """Time and record a step; appends to the active TraceCollector if any.

    On exception, the step is marked status='error' and the exception is
    re-raised. Outside a request (no collector bound) the step still
    measures duration but the record is dropped — convenient for tests
    that don't bother setting up a collector.
    """
    record = StepRecord(name=name, attrs=dict(attrs), started_at_monotonic_ns=time.monotonic_ns())
    log = structlog.get_logger(__name__)
    log.debug("step.start", step=name, **{k: _safe(v) for k, v in attrs.items()})
    try:
        yield record
    except Exception as exc:
        record.status = "error"
        record.error = f"{type(exc).__name__}: {exc}"[:400]
        record.duration_ms = max(
            0, (time.monotonic_ns() - record.started_at_monotonic_ns) // 1_000_000
        )
        log.warning(
            "step.error",
            step=name,
            duration_ms=record.duration_ms,
            error=record.error,
        )
        collector = current_trace()
        if collector is not None:
            collector.add(record)
        raise
    else:
        record.duration_ms = max(
            0, (time.monotonic_ns() - record.started_at_monotonic_ns) // 1_000_000
        )
        log.debug(
            "step.end",
            step=name,
            duration_ms=record.duration_ms,
            status=record.status,
            **{k: _safe(v) for k, v in record.attrs.items()},
        )
        collector = current_trace()
        if collector is not None:
            collector.add(record)


def bind_request_context(**fields: Any) -> None:
    """Bind request_id / conversation_id (etc.) into structlog contextvars."""
    structlog.contextvars.bind_contextvars(**fields)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()


def get_logger(name: str | None = None) -> Any:
    """Return a structlog logger; ``Any`` so call sites don't have to import the type."""
    return structlog.get_logger(name) if name else structlog.get_logger()


_LOG_VALUE_MAX = 200


def _safe(value: Any) -> Any:
    """Truncate / coerce attribute values so we don't blow up the log."""
    if isinstance(value, str) and len(value) > _LOG_VALUE_MAX:
        return value[:_LOG_VALUE_MAX] + "…"
    return value
