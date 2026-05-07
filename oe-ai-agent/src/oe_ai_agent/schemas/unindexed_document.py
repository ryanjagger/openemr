"""Lightweight manifest for documents uploaded but not yet indexed.

Surfaced to the supervisor in its routing prompt so it can decide whether
to hand off to the extractor worker. Intentionally narrow — no PHI beyond
filename + category, which already shows in the file picker the physician
sees.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class UnindexedDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: int
    document_uuid: str
    filename: str
    mimetype: str
    docdate: str | None = None
    category_name: str | None = None
    inferred_document_type: str | None = None
