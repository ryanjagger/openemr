"""Schemas for tool outputs.

Tools return ``list[TypedRow]``. Each row carries a stable resource id, a
patient id (so the verifier can confirm cross-patient binding), a timestamp,
the whitelisted fields the LLM is allowed to see, and an optional verbatim
excerpt for human spot-checking of free-text claims.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TypedRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    resource_type: str
    resource_id: str
    patient_id: str
    last_updated: datetime
    fields: dict[str, Any] = Field(default_factory=dict)
    verbatim_excerpt: str | None = None


class ToolError(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_name: str
    message: str
    status_code: int | None = None
