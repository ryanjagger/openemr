"""Runtime configuration sourced from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    internal_auth_secret: str
    llm_provider: str
    llm_model: str
    anthropic_api_key: str | None
    openemr_fhir_base: str | None


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
    )
