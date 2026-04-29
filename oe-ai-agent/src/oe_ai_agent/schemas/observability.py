"""Response observability envelope — usage + step trace.

Carried inside ``BriefResponse.meta`` and ``ChatTurnResponse.meta`` so the
PHP audit log can persist tokens, cost, latency, and per-step trace
without the sidecar needing its own datastore.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class UsageBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms_total: int = 0


class StepEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    duration_ms: int = 0
    status: Literal["ok", "error"] = "ok"
    error: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)


class ResponseMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    usage: UsageBlock = Field(default_factory=UsageBlock)
    steps: list[StepEntry] = Field(default_factory=list)
