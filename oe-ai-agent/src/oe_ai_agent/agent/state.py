"""Shared graph state for the brief agent.

LangGraph accepts both Pydantic models and TypedDicts; we use a Pydantic
model to match the rest of the codebase and keep secrets/types honest.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from oe_ai_agent.schemas.brief import BriefItem, VerificationFailure
from oe_ai_agent.schemas.tool_results import ToolError, TypedRow


class AgentState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    patient_uuid: str
    fhir_base_url: str
    bearer_token: SecretStr
    request_id: str

    tool_results: list[TypedRow] = Field(default_factory=list)
    fetch_errors: list[ToolError] = Field(default_factory=list)

    raw_llm_output: str | None = None
    parsed_items: list[BriefItem] = Field(default_factory=list)
    parse_error: str | None = None

    verified_items: list[BriefItem] = Field(default_factory=list)
    verification_failures: list[VerificationFailure] = Field(default_factory=list)
