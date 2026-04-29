"""parse_output node — Pydantic-validates raw_llm_output into list[BriefItem]."""

from __future__ import annotations

import json

from pydantic import ValidationError

from oe_ai_agent.agent.state import AgentState
from oe_ai_agent.schemas.brief import BriefItem


async def parse_output_node(state: AgentState) -> dict[str, object]:
    if not state.raw_llm_output:
        return {"parsed_items": [], "parse_error": "raw_llm_output empty"}
    try:
        payload = json.loads(state.raw_llm_output)
    except json.JSONDecodeError as exc:
        return {"parsed_items": [], "parse_error": f"json decode failed: {exc}"}

    raw_items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        return {"parsed_items": [], "parse_error": "missing items[] in response"}

    parsed: list[BriefItem] = []
    rejection_count = 0
    for raw in raw_items:
        try:
            parsed.append(BriefItem.model_validate(raw))
        except ValidationError:
            rejection_count += 1

    if not parsed and rejection_count > 0:
        return {
            "parsed_items": [],
            "parse_error": f"all {rejection_count} items failed BriefItem schema",
        }
    return {"parsed_items": parsed}
