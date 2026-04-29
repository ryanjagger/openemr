"""Schemas for the patient-brief request/response surface.

Mirrors the PHP DTOs in ``oe-module-ai-agent/src/DTO``. The closed
``BriefItem.type`` enum is enforced at the schema layer (Tier 2 verifier in
ARCH §6.2): the model literally cannot emit a free-form claim type through
the structured-output path.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class BriefItemType(StrEnum):
    MED_CURRENT = "med_current"
    MED_CHANGE = "med_change"
    OVERDUE = "overdue"
    RECENT_EVENT = "recent_event"
    AGENDA_ITEM = "agenda_item"
    CODE_STATUS = "code_status"
    ALLERGY = "allergy"


class Citation(BaseModel):
    model_config = ConfigDict(frozen=True)

    resource_type: str
    resource_id: str


class BriefItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: BriefItemType
    text: str
    verbatim_excerpts: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    verified: bool = True


class VerificationFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule: str
    detail: str
    item_index: int | None = None


class BriefRequest(BaseModel):
    """Request from the PHP module.

    ``patient_uuid`` is the FHIR-format identifier (UUID), already resolved
    from OpenEMR's numeric ``pid`` by ``BriefController`` before dispatch.
    """

    patient_uuid: str
    fhir_base_url: str
    bearer_token: SecretStr
    request_id: str


class BriefResponse(BaseModel):
    request_id: str
    model_id: str
    items: list[BriefItem] = Field(default_factory=list)
    verification_failures: list[VerificationFailure] = Field(default_factory=list)

    def model_dump_json_safe(self) -> dict[str, Any]:
        """JSON-safe dump: avoids leaking SecretStr values from any nested fields."""
        return self.model_dump(mode="json")
