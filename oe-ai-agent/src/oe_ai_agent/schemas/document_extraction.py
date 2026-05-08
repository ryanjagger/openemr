"""Schemas for uploaded-document extraction."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from oe_ai_agent.schemas.observability import ResponseMeta

DocumentType = Literal["lab_report", "intake_form"]
IntakeAnswerType = Literal["string", "boolean", "choice", "integer", "decimal", "date"]


class SourceSnippet(BaseModel):
    model_config = ConfigDict(frozen=True)

    page_number: int | None = None
    text: str
    bbox: dict[str, float] | None = None


class ExtractedDocumentFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    fact_type: str
    label: str | None = None
    value_text: str | None = None
    value_numeric: float | None = None
    unit: str | None = None
    observed_on: str | None = None
    question: str | None = None
    answer: str | None = None
    reference_range: str | None = None
    flag: str | None = None
    source_snippets: list[SourceSnippet] = Field(default_factory=list)
    # Intake-form-only fields. These let the PHP ingestion service build a
    # FHIR Questionnaire + QuestionnaireResponse pair instead of a flat fact
    # row. ``link_id`` is the stable join key between definition and response;
    # ``answer_type`` picks the FHIR item type; ``answer_options`` enumerates
    # the choices when ``answer_type='choice'``.
    link_id: str | None = None
    answer_type: IntakeAnswerType | None = None
    answer_options: list[str] | None = None


class DocumentExtractionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    document_uuid: str
    document_type: DocumentType
    filename: str
    mime_type: str
    content_base64: str


class DocumentExtractionResponse(BaseModel):
    request_id: str
    model_id: str
    document_uuid: str
    document_type: DocumentType
    document_summary: str | None = None
    extraction_confidence: float | None = None
    facts: list[ExtractedDocumentFact] = Field(default_factory=list)
    meta: ResponseMeta = Field(default_factory=ResponseMeta)

    def model_dump_json_safe(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
