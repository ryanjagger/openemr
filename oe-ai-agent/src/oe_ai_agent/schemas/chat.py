"""Schemas for the chat (multi-turn) surface.

Mirrors the PHP DTOs in ``oe-module-ai-agent/src/Dto``. Each turn is a
self-contained POST: the PHP layer sends the prior message history along
with the new user turn; the sidecar's conversation store keeps the
fetched FHIR rows out of band so the browser never carries chart data.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from oe_ai_agent.schemas.brief import BriefItem, VerificationFailure


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: ChatRole
    content: str


class ChatRequest(BaseModel):
    """Per-turn request from the PHP module.

    ``conversation_id`` is None on the first turn; the sidecar mints one
    and returns it. Subsequent turns echo it back.
    """

    patient_uuid: str
    fhir_base_url: str
    bearer_token: SecretStr
    request_id: str
    conversation_id: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)


class ChatTurnResponse(BaseModel):
    request_id: str
    conversation_id: str
    model_id: str
    narrative: str
    facts: list[BriefItem] = Field(default_factory=list)
    verification_failures: list[VerificationFailure] = Field(default_factory=list)

    def model_dump_json_safe(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
