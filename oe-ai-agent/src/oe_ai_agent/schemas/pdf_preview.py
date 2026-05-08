"""Schemas for source PDF page previews."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PdfPreviewBbox(BaseModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class PdfPagePreviewRequest(BaseModel):
    content_base64: str
    page: int = Field(default=1, ge=1)
    bbox: PdfPreviewBbox | None = None
    bbox_unit: Literal["normalized", "percent", "pixels"] = "normalized"
