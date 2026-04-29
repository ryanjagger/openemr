"""parse_envelope node — decodes the chat ``ChatTurn`` JSON envelope."""

from __future__ import annotations

import json

from pydantic import ValidationError

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.schemas.brief import BriefItem


async def parse_envelope_node(state: ChatState) -> dict[str, object]:
    if not state.raw_envelope:
        return {
            "parsed_narrative": "",
            "parsed_facts": [],
            "parse_error": "raw_envelope empty",
        }
    try:
        payload = json.loads(state.raw_envelope)
    except json.JSONDecodeError as exc:
        return {
            "parsed_narrative": "",
            "parsed_facts": [],
            "parse_error": f"json decode failed: {exc}",
        }
    if not isinstance(payload, dict):
        return {
            "parsed_narrative": "",
            "parsed_facts": [],
            "parse_error": "envelope was not a JSON object",
        }

    narrative = payload.get("narrative")
    if not isinstance(narrative, str):
        return {
            "parsed_narrative": "",
            "parsed_facts": [],
            "parse_error": "missing or non-string narrative",
        }

    raw_facts_value = payload.get("facts")
    raw_facts = raw_facts_value if isinstance(raw_facts_value, list) else []
    parsed: list[BriefItem] = []
    rejected = 0
    for raw in raw_facts:
        try:
            parsed.append(BriefItem.model_validate(raw))
        except ValidationError:
            rejected += 1

    parse_error: str | None = None
    if not parsed and rejected > 0:
        parse_error = f"all {rejected} facts failed BriefItem schema"

    return {
        "parsed_narrative": narrative,
        "parsed_facts": parsed,
        "parse_error": parse_error,
    }
