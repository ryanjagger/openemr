"""Deterministic bbox localization for extracted document facts.

The LLM extracts evidence text + page numbers but is unreliable at returning
pixel coordinates that line up with the rendered page. This module replaces
LLM-supplied bboxes with bboxes computed from the PDF text layer: it tokenizes
each snippet, slides the token sequence over the page words, and emits a
normalized bbox over the best-matching span. Lab rows are expanded
horizontally so the highlight covers the whole table row, not just the
matched value.

If the page has no text layer (scanned PDFs) or the snippet cannot be
located confidently, the snippet's ``bbox`` stays ``None`` and the UI
renders the page without a highlight.
"""

# mypy: disable-error-code="no-untyped-call"

from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

from oe_ai_agent.schemas.document_extraction import (
    BboxTarget,
    ExtractedDocumentFact,
    SourceSnippet,
)

# Match accepted iff jaccard-style overlap is at least this. Below threshold
# the bbox stays None and the UI shows page + snippet without a highlight.
_MIN_MATCH_CONFIDENCE = 0.55
# Vertical tolerance (in PDF points) for grouping words into the same row
# when expanding a value match to cover the full table row.
_LINE_TOLERANCE_POINTS = 4.0
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[./%-][A-Za-z0-9]+)*")


def localize_facts(
    facts: list[ExtractedDocumentFact],
    *,
    pdf_bytes: bytes,
) -> list[ExtractedDocumentFact]:
    """Return ``facts`` with bbox / bbox_source / bbox_confidence /
    bbox_target filled in on each snippet whose evidence text could be
    located in the PDF text layer.

    Snippets that cannot be located keep ``bbox=None``. Failures here are
    never fatal — the worst-case outcome is that the UI shows the page +
    snippet without an inline highlight, which is what we want anyway when
    we cannot make a confident claim about coordinates.
    """
    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return facts

    try:
        cache: dict[int, list[_Word]] = {}
        return [_localize_fact(fact, document, cache) for fact in facts]
    finally:
        document.close()


@dataclass(frozen=True)
class _Word:
    """One word from PyMuPDF's text-layer extraction.

    PyMuPDF returns ``(x0, y0, x1, y1, text, block, line, word_no)`` tuples.
    We carry the rect, the original text, the line key for row expansion,
    and a normalized form used for matching.
    """

    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block_no: int
    line_no: int
    norm: str


@dataclass(frozen=True)
class _Match:
    start: int
    end: int  # exclusive
    score: float


def _localize_fact(
    fact: ExtractedDocumentFact,
    document: pymupdf.Document,
    cache: dict[int, list[_Word]],
) -> ExtractedDocumentFact:
    target = _bbox_target_for(fact.fact_type)
    new_snippets = [
        _localize_snippet(snippet, document, cache, target=target)
        for snippet in fact.source_snippets
    ]
    return fact.model_copy(update={"source_snippets": new_snippets})


def _localize_snippet(
    snippet: SourceSnippet,
    document: pymupdf.Document,
    cache: dict[int, list[_Word]],
    *,
    target: BboxTarget,
) -> SourceSnippet:
    page_index = (snippet.page_number or 1) - 1
    if page_index < 0 or page_index >= document.page_count:
        return _drop_bbox(snippet)

    words = cache.get(page_index)
    if words is None:
        words = _page_words(document.load_page(page_index))
        cache[page_index] = words
    if not words:
        return _drop_bbox(snippet)

    page = document.load_page(page_index)

    match = _best_match(snippet.text, words)
    if match is None:
        return _drop_bbox(snippet)

    rect = _match_rect(match, words)
    if target == "row":
        rect = _expand_to_row(rect, words)

    bbox = _normalize_bbox(rect, page)
    if bbox is None:
        return _drop_bbox(snippet)

    return snippet.model_copy(
        update={
            "bbox": bbox,
            "bbox_source": "text_layer",
            "bbox_confidence": round(match.score, 3),
            "bbox_target": target,
        }
    )


def _drop_bbox(snippet: SourceSnippet) -> SourceSnippet:
    """Clear any bbox + metadata so the UI renders without a highlight.

    We never trust an inbound bbox from the LLM here — the whole point of
    the localizer is that LLM bboxes are unreliable. If we cannot localize
    deterministically, the right answer is "no highlight."
    """
    return snippet.model_copy(
        update={
            "bbox": None,
            "bbox_source": None,
            "bbox_confidence": None,
            "bbox_target": None,
        }
    )


def _bbox_target_for(fact_type: str) -> BboxTarget:
    if fact_type == "lab_result":
        return "row"
    if fact_type == "intake_answer":
        return "field"
    return "snippet"


def _page_words(page: pymupdf.Page) -> list[_Word]:
    raw = page.get_text("words")
    out: list[_Word] = []
    for entry in raw:
        x0, y0, x1, y1, text, block_no, line_no, _word_no = entry
        # PyMuPDF returns one entry per whitespace-delimited token, but
        # punctuation can be glued on ("Glucose:", "95.4", "(H)"). Run the
        # same token regex used on the snippet so attached punctuation
        # does not block matches.
        sub_tokens = _tokenize(str(text))
        if not sub_tokens:
            continue
        # Keep the original rect for every sub-token; bbox accuracy is
        # dominated by the row-expansion step, and splitting the rect
        # proportionally would just add noise.
        for sub in sub_tokens:
            out.append(
                _Word(
                    x0=float(x0),
                    y0=float(y0),
                    x1=float(x1),
                    y1=float(y1),
                    text=str(text),
                    block_no=int(block_no),
                    line_no=int(line_no),
                    norm=sub,
                )
            )
    return out


def _best_match(target_text: str, words: list[_Word]) -> _Match | None:
    target_tokens = _tokenize(target_text)
    if not target_tokens:
        return None

    target_set = set(target_tokens)
    n_target = len(target_tokens)
    n_words = len(words)

    # Try a few window sizes around the snippet length so we still match
    # when the snippet drops or duplicates a word vs. the page text.
    candidates = {
        n_target,
        max(1, n_target - 1),
        n_target + 1,
        n_target + 2,
    }

    best: _Match | None = None
    for window in sorted(candidates):
        if window <= 0 or window > n_words:
            continue
        for start in range(0, n_words - window + 1):
            slice_tokens = [w.norm for w in words[start : start + window]]
            slice_set = set(slice_tokens)
            overlap = len(target_set & slice_set)
            if overlap == 0:
                continue
            denom = max(len(target_set), len(slice_set))
            score = overlap / denom
            # Tie-break by preferring matches that preserve token order.
            if score == 1.0 and slice_tokens != target_tokens[:window]:
                score -= 0.001
            if best is None or score > best.score:
                best = _Match(start=start, end=start + window, score=score)

    if best is None or best.score < _MIN_MATCH_CONFIDENCE:
        return None
    return best


def _match_rect(
    match: _Match,
    words: list[_Word],
) -> tuple[float, float, float, float]:
    matched = words[match.start : match.end]
    return (
        min(w.x0 for w in matched),
        min(w.y0 for w in matched),
        max(w.x1 for w in matched),
        max(w.y1 for w in matched),
    )


def _expand_to_row(
    rect: tuple[float, float, float, float],
    words: list[_Word],
) -> tuple[float, float, float, float]:
    """Expand a tight bbox to cover all words on the same visual line.

    Lab tables have the test name on the left and the value/unit/range on
    the right; users want the highlight to span the whole row, not just
    the cell that matched the snippet.
    """
    cy = (rect[1] + rect[3]) / 2.0
    line_words = [
        w
        for w in words
        if w.y0 - _LINE_TOLERANCE_POINTS <= cy <= w.y1 + _LINE_TOLERANCE_POINTS
    ]
    if not line_words:
        return rect

    return (
        min(w.x0 for w in line_words),
        min(rect[1], *(w.y0 for w in line_words)),
        max(w.x1 for w in line_words),
        max(rect[3], *(w.y1 for w in line_words)),
    )


def _normalize_bbox(
    rect: tuple[float, float, float, float],
    page: pymupdf.Page,
) -> dict[str, float] | None:
    page_rect = page.rect
    width = page_rect.width
    height = page_rect.height
    if width <= 0 or height <= 0:
        return None

    left = max(rect[0] - page_rect.x0, 0.0)
    top = max(rect[1] - page_rect.y0, 0.0)
    right = min(rect[2] - page_rect.x0, width)
    bottom = min(rect[3] - page_rect.y0, height)
    if right - left <= 0 or bottom - top <= 0:
        return None

    return {
        "x": round(left / width, 6),
        "y": round(top / height, 6),
        "width": round((right - left) / width, 6),
        "height": round((bottom - top) / height, 6),
    }


def _tokenize(text: str) -> list[str]:
    return [_normalize_token(match) for match in _TOKEN_PATTERN.findall(text)]


def _normalize_token(text: str) -> str:
    return text.strip().lower()
