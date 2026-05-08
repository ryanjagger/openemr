"""Graph state for the chat agent."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from oe_ai_agent.schemas.brief import VerificationFailure
from oe_ai_agent.schemas.chat import ChatFact, ChatMessage
from oe_ai_agent.schemas.tool_results import ToolError, TypedRow
from oe_ai_agent.schemas.unindexed_document import UnindexedDocument


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

    unindexed_documents: list[UnindexedDocument] = Field(default_factory=list)

    supervisor_decisions: list[str] = Field(default_factory=list)
    supervisor_turns_remaining: int = 6
    extractor_runs: int = 0
    evidence_runs: int = 0
    # True when the extractor's extract_documents tool poll-timed out before
    # the ingestion job reached a terminal status. The job is still running
    # in the background; the freshly-extracted rows are not yet visible in
    # FHIR, so finalize should tell the user to retry in ~30s rather than
    # claim "no data".
    extraction_pending: bool = False

    raw_envelope: str | None = None
    parsed_narrative: str = ""
    parsed_facts: list[ChatFact] = Field(default_factory=list)
    parse_error: str | None = None

    verified_facts: list[ChatFact] = Field(default_factory=list)
    verification_failures: list[VerificationFailure] = Field(default_factory=list)
