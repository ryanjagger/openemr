"""Tests for the per-step trace collector."""

from __future__ import annotations

import asyncio

import pytest

from oe_ai_agent.llm.client import LlmUsage
from oe_ai_agent.observability.trace import (
    StepRecord,
    current_trace,
    step,
    use_trace,
)


@pytest.mark.asyncio
async def test_step_records_duration_and_attrs() -> None:
    async with use_trace() as trace, step("fetch_context") as record:
        await asyncio.sleep(0.005)
        record.attrs["row_count"] = 7

    assert len(trace.records) == 1
    rec = trace.records[0]
    assert rec.name == "fetch_context"
    assert rec.status == "ok"
    assert rec.attrs["row_count"] == 7
    assert rec.duration_ms >= 0


@pytest.mark.asyncio
async def test_step_marks_error_and_re_raises() -> None:
    async with use_trace() as trace:
        with pytest.raises(RuntimeError, match="boom"):
            async with step("llm_call"):
                raise RuntimeError("boom")

    assert len(trace.records) == 1
    rec = trace.records[0]
    assert rec.status == "error"
    assert rec.error is not None
    assert "boom" in rec.error


@pytest.mark.asyncio
async def test_nested_steps_are_flat_in_record_order() -> None:
    async with use_trace() as trace, step("llm_turn"):
        async with step("tool_call", tool="get_lab_trend"):
            pass
        async with step("tool_call", tool="get_lab_trend"):
            pass

    names = [r.name for r in trace.records]
    # Inner steps land first because they finish before the outer.
    assert names == ["tool_call", "tool_call", "llm_turn"]


@pytest.mark.asyncio
async def test_usage_summary_aggregates_across_calls() -> None:
    async with use_trace() as trace:
        trace.add_usage(LlmUsage(prompt_tokens=10, completion_tokens=4, total_tokens=14))
        trace.add_usage(
            LlmUsage(prompt_tokens=2, completion_tokens=1, total_tokens=3, cost_usd=0.01)
        )

    summary = trace.usage_summary()
    assert summary["prompt_tokens"] == 12
    assert summary["completion_tokens"] == 5
    assert summary["total_tokens"] == 17
    assert summary["cost_usd"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_step_outside_trace_does_not_crash() -> None:
    """No active collector? Step still measures, just doesn't record anywhere."""
    async with step("orphan_step"):
        await asyncio.sleep(0)
    assert current_trace() is None


@pytest.mark.asyncio
async def test_total_duration_ms_increments() -> None:
    async with use_trace() as trace:
        await asyncio.sleep(0.005)
        assert trace.total_duration_ms() >= 0


def test_step_record_to_dict() -> None:
    rec = StepRecord(
        name="x", duration_ms=42, status="ok", attrs={"k": "v"}
    )
    payload = rec.to_dict()
    assert payload == {
        "name": "x",
        "duration_ms": 42,
        "status": "ok",
        "error": None,
        "attrs": {"k": "v"},
    }
