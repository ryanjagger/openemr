"""Render PDF source previews with optional extracted-field highlighting."""

# mypy: disable-error-code="no-untyped-call"

from __future__ import annotations

import base64
import binascii

import pymupdf

from oe_ai_agent.schemas.pdf_preview import PdfPagePreviewRequest, PdfPreviewBbox

RENDER_SCALE = 2.0
MIN_BBOX_SIDE_POINTS = 0.5
STROKE_RGB = (0.8627, 0.2078, 0.2706)


class PdfPreviewError(ValueError):
    """Raised when a PDF page preview cannot be rendered."""


def render_pdf_page_preview(request: PdfPagePreviewRequest) -> bytes:
    try:
        pdf_bytes = base64.b64decode(request.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PdfPreviewError("invalid_pdf_base64") from exc

    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise PdfPreviewError("invalid_pdf") from exc

    try:
        if request.page > document.page_count:
            raise PdfPreviewError("page_not_found")

        page = document.load_page(request.page - 1)
        if request.bbox is not None:
            _draw_bbox(page, request.bbox, request.bbox_unit)

        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(RENDER_SCALE, RENDER_SCALE),
            alpha=False,
        )
        return bytes(pixmap.tobytes("png"))
    finally:
        document.close()


def _draw_bbox(page: pymupdf.Page, bbox: PdfPreviewBbox, unit: str) -> None:
    page_rect = page.rect
    if unit == "normalized":
        scale_x = page_rect.width
        scale_y = page_rect.height
    elif unit == "percent":
        scale_x = page_rect.width / 100.0
        scale_y = page_rect.height / 100.0
    else:
        scale_x = 1.0 / RENDER_SCALE
        scale_y = 1.0 / RENDER_SCALE

    left = _clamp(page_rect.x0 + bbox.x * scale_x, page_rect.x0, page_rect.x1)
    top = _clamp(page_rect.y0 + bbox.y * scale_y, page_rect.y0, page_rect.y1)
    right = _clamp(
        page_rect.x0 + (bbox.x + bbox.width) * scale_x,
        page_rect.x0,
        page_rect.x1,
    )
    bottom = _clamp(
        page_rect.y0 + (bbox.y + bbox.height) * scale_y,
        page_rect.y0,
        page_rect.y1,
    )
    if right - left < MIN_BBOX_SIDE_POINTS or bottom - top < MIN_BBOX_SIDE_POINTS:
        return

    page.draw_rect(
        pymupdf.Rect(left, top, right, bottom),
        color=STROKE_RGB,
        width=max(1.5, min(page_rect.width, page_rect.height) * 0.003),
    )


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))
