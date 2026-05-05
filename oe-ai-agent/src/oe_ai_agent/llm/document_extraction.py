"""LLM-backed extraction of uploaded PDFs/PNGs into grounded document facts."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from oe_ai_agent.llm.client import LlmClient, LlmUsage
from oe_ai_agent.schemas.document_extraction import (
    DocumentExtractionRequest,
    DocumentExtractionResponse,
    ExtractedDocumentFact,
    SourceSnippet,
)


class _ExtractionEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_summary: str | None = None
    extraction_confidence: float | None = None
    facts: list[ExtractedDocumentFact] = Field(default_factory=list)


async def extract_document_with_llm(
    llm: LlmClient,
    request: DocumentExtractionRequest,
) -> tuple[_ExtractionEnvelope, LlmUsage]:
    """Extract document facts, returning the envelope and LLM usage.

    The mock provider intentionally avoids inspecting PHI-bearing document
    bytes and returns a small deterministic envelope so local development can
    exercise the ingestion path without live LLM calls.
    """
    if llm.model_id == "mock":
        completion = await _mock_extraction(llm, request)
        return completion

    result = await llm.chat(
        _build_messages(request, model_id=llm.model_id),
        response_format=_response_format(),
    )
    try:
        decoded = json.loads(result.content)
        envelope = _ExtractionEnvelope.model_validate(decoded)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError("document extraction response was not valid extraction JSON") from exc

    return envelope, result.usage


async def _mock_extraction(
    llm: LlmClient,
    request: DocumentExtractionRequest,
) -> tuple[_ExtractionEnvelope, LlmUsage]:
    if request.document_type == "lab_report":
        fact = ExtractedDocumentFact(
            fact_type="lab_result",
            label="Mock lab extraction",
            value_text="No live LLM configured; document bytes were not inspected.",
            source_snippets=[
                SourceSnippet(
                    page_number=None,
                    text=f"Mock extraction placeholder for {request.filename}",
                )
            ],
        )
    else:
        fact = ExtractedDocumentFact(
            fact_type="intake_answer",
            label="Mock intake extraction",
            question="Document extraction status",
            answer="No live LLM configured; document bytes were not inspected.",
            source_snippets=[
                SourceSnippet(
                    page_number=None,
                    text=f"Mock extraction placeholder for {request.filename}",
                )
            ],
        )
    result = await llm.chat(
        [{"role": "user", "content": "mock document extraction"}],
        response_format=None,
    )
    return (
        _ExtractionEnvelope(
            document_summary="Mock extraction placeholder.",
            extraction_confidence=0.0,
            facts=[fact],
        ),
        result.usage,
    )


def _build_messages(
    request: DocumentExtractionRequest,
    *,
    model_id: str,
) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": (
                "You extract facts from one uploaded OpenEMR patient document. "
                "Return only JSON matching the schema. Do not diagnose, advise, "
                "or infer facts not visible in the document."
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": _instruction_text(request),
                },
                _document_block(request, model_id=model_id),
            ],
        },
    ]


def _instruction_text(request: DocumentExtractionRequest) -> str:
    if request.document_type == "lab_report":
        return (
            f"Document filename: {request.filename}\n"
            "Document type confirmed by user: lab_report\n\n"
            "Extract every lab row visible in the document. For each row, use "
            "fact_type='lab_result', label as the test/analyte name, value_text "
            "as the displayed result, value_numeric only when the displayed "
            "result is a plain number, unit when present, reference_range when "
            "present, flag when present, observed_on as YYYY-MM-DD when visible, "
            "and source_snippets with short verbatim evidence and page_number."
        )
    return (
        f"Document filename: {request.filename}\n"
        "Document type confirmed by user: intake_form\n\n"
        "Extract every question-answer pair visible in the patient intake form. "
        "For each answer, use fact_type='intake_answer', label as a short field "
        "name, question as the form question, answer and value_text as the "
        "patient response, and source_snippets with short verbatim evidence and "
        "page_number."
    )


def _document_block(request: DocumentExtractionRequest, *, model_id: str) -> dict[str, Any]:
    if model_id.startswith("anthropic/"):
        if request.mime_type == "application/pdf":
            return {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": request.mime_type,
                    "data": request.content_base64,
                },
            }
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": request.mime_type,
                "data": request.content_base64,
            },
        }

    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{request.mime_type};base64,{request.content_base64}",
        },
    }


def _response_format() -> dict[str, Any]:
    snippet_schema = {
        "type": "object",
        "properties": {
            "page_number": {"type": ["integer", "null"]},
            "text": {"type": "string"},
            "bbox": {
                "type": ["object", "null"],
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "width": {"type": "number"},
                    "height": {"type": "number"},
                },
                "additionalProperties": False,
            },
        },
        "required": ["page_number", "text", "bbox"],
        "additionalProperties": False,
    }
    fact_schema = {
        "type": "object",
        "properties": {
            "fact_type": {"type": "string"},
            "label": {"type": ["string", "null"]},
            "value_text": {"type": ["string", "null"]},
            "value_numeric": {"type": ["number", "null"]},
            "unit": {"type": ["string", "null"]},
            "observed_on": {"type": ["string", "null"]},
            "question": {"type": ["string", "null"]},
            "answer": {"type": ["string", "null"]},
            "reference_range": {"type": ["string", "null"]},
            "flag": {"type": ["string", "null"]},
            "source_snippets": {"type": "array", "items": snippet_schema},
        },
        "required": [
            "fact_type",
            "label",
            "value_text",
            "value_numeric",
            "unit",
            "observed_on",
            "question",
            "answer",
            "reference_range",
            "flag",
            "source_snippets",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "DocumentExtraction",
            "schema": {
                "type": "object",
                "properties": {
                    "document_summary": {"type": ["string", "null"]},
                    "extraction_confidence": {"type": ["number", "null"]},
                    "facts": {"type": "array", "items": fact_schema},
                },
                "required": ["document_summary", "extraction_confidence", "facts"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }


def to_response(
    request: DocumentExtractionRequest,
    model_id: str,
    envelope: _ExtractionEnvelope,
    meta: Any,
) -> DocumentExtractionResponse:
    return DocumentExtractionResponse(
        request_id=request.request_id,
        model_id=model_id,
        document_uuid=request.document_uuid,
        document_type=request.document_type,
        document_summary=envelope.document_summary,
        extraction_confidence=envelope.extraction_confidence,
        facts=envelope.facts,
        meta=meta,
    )
