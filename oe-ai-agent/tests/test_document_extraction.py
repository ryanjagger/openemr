"""Tests for uploaded-document extraction helpers."""

from __future__ import annotations

import json
from typing import Any

from oe_ai_agent.llm.client import LlmCompletionResult, LlmUsage
from oe_ai_agent.llm.document_extraction import _build_messages, extract_document_with_llm
from oe_ai_agent.llm.mock_client import MockLlmClient
from oe_ai_agent.schemas.document_extraction import DocumentExtractionRequest


def _request(mime_type: str = "application/pdf") -> DocumentExtractionRequest:
    return DocumentExtractionRequest(
        request_id="req-1",
        document_uuid="doc-uuid-1",
        document_type="lab_report",
        filename="lab.pdf",
        mime_type=mime_type,
        content_base64="cGRmLWJ5dGVz",
    )


async def test_mock_document_extraction_returns_placeholder_without_reading_bytes() -> None:
    llm = MockLlmClient(
        scripted="{}",
        default_usage=LlmUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
    )

    envelope, usage = await extract_document_with_llm(llm, _request())

    assert envelope.extraction_confidence == 0.0
    assert len(envelope.facts) == 1
    assert envelope.facts[0].fact_type == "lab_result"
    assert envelope.facts[0].source_snippets[0].text == "Mock extraction placeholder for lab.pdf"
    assert usage.total_tokens == 3


def test_anthropic_pdf_message_uses_document_block() -> None:
    messages = _build_messages(_request(), model_id="anthropic/claude-sonnet-4-6")

    content = messages[1]["content"]
    assert isinstance(content, list)
    document_block = content[1]
    assert document_block["type"] == "document"
    assert document_block["source"]["media_type"] == "application/pdf"
    assert document_block["source"]["data"] == "cGRmLWJ5dGVz"


def test_anthropic_png_message_uses_image_url_block() -> None:
    messages = _build_messages(_request("image/png"), model_id="anthropic/claude-sonnet-4-6")

    content = messages[1]["content"]
    assert isinstance(content, list)
    image_block = content[1]
    assert image_block["type"] == "image_url"
    assert image_block["image_url"]["url"] == "data:image/png;base64,cGRmLWJ5dGVz"


async def test_live_document_extraction_uses_larger_document_token_budget() -> None:
    llm = _CapturingLlm()

    envelope, _usage = await extract_document_with_llm(llm, _request())

    assert envelope.facts == []
    assert llm.max_tokens == 8192


class _CapturingLlm:
    model_id = "anthropic/claude-sonnet-4-6"

    def __init__(self) -> None:
        self.max_tokens: int | None = None

    async def chat(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
        *,
        max_tokens: int | None = None,
    ) -> LlmCompletionResult:
        del messages, response_format
        self.max_tokens = max_tokens
        return LlmCompletionResult(
            content=json.dumps(
                {
                    "document_summary": "No facts.",
                    "extraction_confidence": 1.0,
                    "facts": [],
                }
            ),
            usage=LlmUsage(),
        )
