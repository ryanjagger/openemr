"""Tests for the deterministic bbox localizer."""

# mypy: disable-error-code="no-untyped-call"

from __future__ import annotations

import pymupdf

from oe_ai_agent.documents.bbox_localizer import localize_facts
from oe_ai_agent.schemas.document_extraction import (
    ExtractedDocumentFact,
    SourceSnippet,
)


def _build_lab_report_pdf() -> bytes:
    """Render a single-page PDF with two simple lab rows.

    Cells are placed at explicit x positions so the row spans most of the
    page width — this is what makes the row-expansion behavior observable
    (a tight match on "Glucose 95 mg/dL" alone would only cover ~30%).
    """
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((72, 72), "Patient Lab Report", fontsize=14)

    page.insert_text((72, 144), "Glucose", fontsize=11)
    page.insert_text((220, 144), "95 mg/dL", fontsize=11)
    page.insert_text((360, 144), "70-99", fontsize=11)
    page.insert_text((500, 144), "Normal", fontsize=11)

    page.insert_text((72, 168), "Hemoglobin A1c", fontsize=11)
    page.insert_text((220, 168), "5.6 %", fontsize=11)
    page.insert_text((360, 168), "<5.7", fontsize=11)
    page.insert_text((500, 168), "Normal", fontsize=11)

    pdf_bytes = document.tobytes()
    document.close()
    return pdf_bytes


def _build_intake_pdf() -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((72, 100), "Q1. Do you smoke tobacco?  Answer: No", fontsize=12)
    page.insert_text((72, 140), "Q2. List current medications: Lisinopril 10mg daily", fontsize=12)
    pdf_bytes = document.tobytes()
    document.close()
    return pdf_bytes


def test_localizer_finds_lab_row_and_expands_to_full_line() -> None:
    pdf_bytes = _build_lab_report_pdf()
    facts = [
        ExtractedDocumentFact(
            fact_type="lab_result",
            label="Glucose",
            value_text="95",
            unit="mg/dL",
            source_snippets=[
                # Snippet text matches what's printed verbatim on the page.
                SourceSnippet(page_number=1, text="Glucose 95 mg/dL"),
            ],
        ),
    ]

    [located] = localize_facts(facts, pdf_bytes=pdf_bytes)
    snippet = located.source_snippets[0]
    assert snippet.bbox is not None
    assert snippet.bbox_source == "text_layer"
    assert snippet.bbox_target == "row"
    assert snippet.bbox_confidence is not None
    assert snippet.bbox_confidence >= 0.55

    bbox = snippet.bbox
    # Normalized to page width/height.
    assert 0.0 <= bbox["x"] <= 0.2
    assert 0.0 < bbox["y"] < 1.0
    # Row expansion should make the bbox span ~most of the page width
    # (the rightmost word "Normal" sits past the 60% mark) — this is the
    # behavior that distinguishes "row" target from "field" target.
    assert bbox["width"] > 0.4


def test_localizer_finds_intake_field_without_row_expansion() -> None:
    pdf_bytes = _build_intake_pdf()
    facts = [
        ExtractedDocumentFact(
            fact_type="intake_answer",
            question="Do you smoke tobacco?",
            answer="No",
            source_snippets=[
                SourceSnippet(page_number=1, text="Do you smoke tobacco? Answer: No"),
            ],
        ),
    ]

    [located] = localize_facts(facts, pdf_bytes=pdf_bytes)
    snippet = located.source_snippets[0]
    assert snippet.bbox is not None
    assert snippet.bbox_target == "field"
    assert snippet.bbox_source == "text_layer"


def test_localizer_drops_bbox_when_snippet_text_not_on_page() -> None:
    pdf_bytes = _build_lab_report_pdf()
    facts = [
        ExtractedDocumentFact(
            fact_type="lab_result",
            label="Glucose",
            value_text="95",
            source_snippets=[
                SourceSnippet(
                    page_number=1,
                    text="Cholesterol 220 mg/dL high",  # not on the page
                ),
            ],
        ),
    ]

    [located] = localize_facts(facts, pdf_bytes=pdf_bytes)
    snippet = located.source_snippets[0]
    assert snippet.bbox is None
    assert snippet.bbox_source is None
    assert snippet.bbox_confidence is None
    assert snippet.bbox_target is None


def test_localizer_clears_stale_llm_bbox_when_text_layer_match_fails() -> None:
    """A bad LLM-supplied bbox must not survive into the output."""
    pdf_bytes = _build_lab_report_pdf()
    facts = [
        ExtractedDocumentFact(
            fact_type="lab_result",
            label="Glucose",
            source_snippets=[
                SourceSnippet(
                    page_number=1,
                    text="entirely unrelated text",
                    bbox={"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.05},
                    bbox_source="llm",
                    bbox_confidence=0.9,
                ),
            ],
        ),
    ]

    [located] = localize_facts(facts, pdf_bytes=pdf_bytes)
    snippet = located.source_snippets[0]
    assert snippet.bbox is None
    assert snippet.bbox_source is None


def test_localizer_returns_facts_unchanged_for_invalid_pdf_bytes() -> None:
    facts = [
        ExtractedDocumentFact(
            fact_type="lab_result",
            label="Glucose",
            source_snippets=[SourceSnippet(page_number=1, text="Glucose 95")],
        ),
    ]

    [located] = localize_facts(facts, pdf_bytes=b"not-a-pdf")
    assert located.source_snippets[0].bbox is None


def test_localizer_handles_page_number_out_of_range() -> None:
    pdf_bytes = _build_lab_report_pdf()
    facts = [
        ExtractedDocumentFact(
            fact_type="lab_result",
            label="Glucose",
            source_snippets=[SourceSnippet(page_number=99, text="Glucose 95 mg/dL")],
        ),
    ]

    [located] = localize_facts(facts, pdf_bytes=pdf_bytes)
    assert located.source_snippets[0].bbox is None
