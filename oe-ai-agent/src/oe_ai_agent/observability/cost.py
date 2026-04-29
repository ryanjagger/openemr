"""LLM cost calculation — thin wrapper over ``litellm.completion_cost``.

LiteLLM ships a pricing table that covers every model it routes to.
``completion_cost(completion_response=...)`` returns USD as a float.
We wrap it because the unhappy paths (unknown model, missing usage,
mock-mode responses) all benefit from being normalized to 0.0 rather
than crashing the request — observability data is best-effort and must
never break the user-facing path.
"""

from __future__ import annotations

import logging
from typing import Any

import litellm

logger = logging.getLogger(__name__)


def compute_completion_cost(response: Any) -> float:
    """Return USD cost for a litellm completion response, or 0.0 on miss.

    ``response`` is whatever ``litellm.acompletion`` produced — typically a
    ``ModelResponse`` object but sometimes a dict in older versions.
    """
    try:
        cost = litellm.completion_cost(completion_response=response)
    except Exception as exc:
        logger.debug("completion_cost failed: %s", exc)
        return 0.0
    try:
        return float(cost)
    except (TypeError, ValueError):
        return 0.0


def usd_to_micros(cost_usd: float) -> int:
    """Convert USD float → integer micros (1 USD = 1_000_000 micros).

    Stored as ``cost_usd_micros BIGINT`` in MySQL to avoid float drift.
    """
    if cost_usd <= 0.0:
        return 0
    return round(cost_usd * 1_000_000)
