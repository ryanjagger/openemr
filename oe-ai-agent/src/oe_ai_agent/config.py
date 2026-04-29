"""Runtime configuration sourced from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

from oe_ai_agent.schemas.brief import BriefItemType

# Free-text-derived item types are gated behind AI_AGENT_ENABLE_FREETEXT_TYPES.
# Tier 1's typed-fact re-extraction can string-match structured fields but
# cannot detect negation/temporality drift in prose ("denies chest pain"
# rendered as "has chest pain"). Per ARCH §6.5 #4, this is the largest
# residual MVP risk; the systemic fix is Tier 3 paraphrase fidelity, deferred.
FREETEXT_ITEM_TYPES: frozenset[BriefItemType] = frozenset(
    {BriefItemType.RECENT_EVENT, BriefItemType.AGENDA_ITEM},
)


@dataclass(frozen=True)
class Settings:
    internal_auth_secret: str
    llm_provider: str
    llm_model: str
    anthropic_api_key: str | None
    openemr_fhir_base: str | None
    enable_freetext_types: bool

    @property
    def allowed_item_types(self) -> frozenset[BriefItemType]:
        if self.enable_freetext_types:
            return frozenset(BriefItemType)
        return frozenset(BriefItemType) - FREETEXT_ITEM_TYPES


def load_settings() -> Settings:
    secret = os.environ.get("INTERNAL_AUTH_SECRET")
    if not secret:
        raise RuntimeError(
            "INTERNAL_AUTH_SECRET is required; refusing to start the sidecar without it.",
        )
    return Settings(
        internal_auth_secret=secret,
        llm_provider=os.environ.get("LLM_PROVIDER", "mock"),
        llm_model=os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-6"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
        openemr_fhir_base=os.environ.get("OPENEMR_FHIR_BASE"),
        enable_freetext_types=_parse_bool(os.environ.get("AI_AGENT_ENABLE_FREETEXT_TYPES")),
    )


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}
