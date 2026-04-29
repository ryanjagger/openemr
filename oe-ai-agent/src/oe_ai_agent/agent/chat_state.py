"""Graph state for the chat agent."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from oe_ai_agent.schemas.brief import BriefItem, VerificationFailure
from oe_ai_agent.schemas.chat import ChatMessage
from oe_ai_agent.schemas.tool_results import ToolError, TypedRow


class ChatState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    patient_uuid: str
    fhir_base_url: str
    bearer_token: SecretStr
    request_id: str
    conversation_id: str
    history: list[ChatMessage] = Field(default_factory=list)

    cached_context: list[TypedRow] = Field(default_factory=list)
    fetch_errors: list[ToolError] = Field(default_factory=list)

    raw_envelope: str | None = None
    parsed_narrative: str = ""
    parsed_facts: list[BriefItem] = Field(default_factory=list)
    parse_error: str | None = None

    verified_facts: list[BriefItem] = Field(default_factory=list)
    verification_failures: list[VerificationFailure] = Field(default_factory=list)
