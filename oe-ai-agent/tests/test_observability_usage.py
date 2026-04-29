"""Tests for LLM usage capture + cost helper."""

from __future__ import annotations

from oe_ai_agent.llm.client import LlmUsage
from oe_ai_agent.observability.cost import compute_completion_cost, usd_to_micros


def test_compute_completion_cost_handles_dict_response() -> None:
    """Passing a dict response should not raise; returns 0.0 if pricing is unknown."""
    cost = compute_completion_cost(
        {
            "model": "anthropic/claude-sonnet-4-6",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }
    )
    assert cost >= 0.0


def test_compute_completion_cost_handles_garbage() -> None:
    assert compute_completion_cost(None) == 0.0
    assert compute_completion_cost({}) == 0.0
    assert compute_completion_cost("not a response") == 0.0


def test_usd_to_micros_round_trip() -> None:
    assert usd_to_micros(0.0) == 0
    assert usd_to_micros(0.000001) == 1
    assert usd_to_micros(1.0) == 1_000_000
    assert usd_to_micros(0.0123456) == 12_346  # rounded
    # Negatives are clamped to 0 so the BIGINT UNSIGNED column never errors.
    assert usd_to_micros(-1.0) == 0


def test_llm_usage_merge() -> None:
    a = LlmUsage(
        prompt_tokens=10,
        completion_tokens=4,
        total_tokens=14,
        cost_usd=0.001,
        latency_ms=100,
    )
    b = LlmUsage(
        prompt_tokens=5,
        completion_tokens=2,
        total_tokens=7,
        cost_usd=0.0005,
        latency_ms=80,
    )
    merged = a.merge(b)
    assert merged.prompt_tokens == 15
    assert merged.completion_tokens == 6
    assert merged.total_tokens == 21
    assert merged.cost_usd == 0.0015
    assert merged.latency_ms == 180
