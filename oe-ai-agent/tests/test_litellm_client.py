"""Tests for provider response normalization in LiteLLMClient."""

from __future__ import annotations

from types import SimpleNamespace

from oe_ai_agent.llm.litellm_client import (
    _extract_anthropic_content_tool_calls,
    _redact_large_multimodal_payloads,
)


def test_extracts_anthropic_tool_use_content_blocks() -> None:
    calls = _extract_anthropic_content_tool_calls(
        [
            {"type": "text", "text": "I need to check the chart."},
            {
                "type": "tool_use",
                "id": "toolu_123",
                "name": "get_immunizations",
                "input": {"status": "completed"},
            },
        ]
    )

    assert len(calls) == 1
    assert calls[0].tool_call_id == "toolu_123"
    assert calls[0].name == "get_immunizations"
    assert calls[0].arguments == {"status": "completed"}


def test_ignores_non_tool_content_blocks() -> None:
    assert _extract_anthropic_content_tool_calls("plain text") == []
    assert _extract_anthropic_content_tool_calls([{"type": "text", "text": "hi"}]) == []


def test_extracts_anthropic_tool_use_object_blocks() -> None:
    calls = _extract_anthropic_content_tool_calls(
        [
            SimpleNamespace(type="text", text="I need to check the chart."),
            SimpleNamespace(
                type="tool_use",
                id="toolu_456",
                name="get_immunizations",
                input={"limit": 50},
            ),
        ]
    )

    assert len(calls) == 1
    assert calls[0].tool_call_id == "toolu_456"
    assert calls[0].name == "get_immunizations"
    assert calls[0].arguments == {"limit": 50}


def test_redacts_base64_document_payloads_from_observability_input() -> None:
    redacted = _redact_large_multimodal_payloads(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "extract this"},
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": "abc123",
                        },
                    },
                ],
            }
        ]
    )

    content = redacted[0]["content"]
    assert isinstance(content, list)
    source = content[1]["source"]
    assert source["data"] == "<base64 redacted; 6 chars>"
