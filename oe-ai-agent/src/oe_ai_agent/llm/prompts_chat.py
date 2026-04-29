"""Prompt construction for the chat (multi-turn) surface.

Builds on ``llm.prompts`` but emits a different envelope: a free-prose
``narrative`` plus a typed ``facts: BriefItem[]`` array. The narrative is
what the physician reads; the facts are what the verifier checks. Numbers
and dates in the narrative MUST appear verbatim in some fact's
``verbatim_excerpts`` — that's enforced by ``check_narrative_grounding``.
"""

from __future__ import annotations

from typing import Any

from oe_ai_agent.llm.prompts import _detail_for_row, _note_for_row
from oe_ai_agent.schemas.brief import BriefItemType
from oe_ai_agent.schemas.chat import ChatMessage
from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.verifier.constraints import ALLOWED_TABLES_FOR_TYPE


def build_chat_messages(
    *,
    patient_uuid: str,
    cached_context: list[TypedRow],
    history: list[ChatMessage],
    allowed_types: frozenset[BriefItemType],
) -> list[dict[str, Any]]:
    """Assemble the message list for a chat turn.

    The system message encodes the envelope contract and the safety rules.
    A single user message carries the chart context; subsequent messages
    are the actual conversation history. This preserves the prompt-cache
    boundary even though we don't yet enable Anthropic prompt caching.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _chat_system_prompt(allowed_types)},
        {
            "role": "user",
            "content": _chart_context_prompt(patient_uuid, cached_context),
        },
    ]
    for msg in history:
        messages.append({"role": msg.role.value, "content": msg.content})
    return messages


def chat_response_format(
    allowed_types: frozenset[BriefItemType],
) -> dict[str, Any]:
    """Pinned JSON schema for the chat envelope: narrative + facts."""
    ordered = [t.value for t in BriefItemType if t in allowed_types]
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ChatTurn",
            "schema": {
                "type": "object",
                "properties": {
                    "narrative": {"type": "string"},
                    "facts": {
                        "type": "array",
                        "items": _fact_schema(ordered),
                    },
                },
                "required": ["narrative", "facts"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }


def chat_tools_schema() -> list[dict[str, Any]]:
    """Function-calling tool catalog exposed to the chat agent.

    Adding a tool here means: define it in ``tools/``, register it in
    ``agent/nodes/tool_loop.py::_TOOL_HANDLERS``, and add an entry below.
    Today: one drill-down tool. The chatbot's pre-fetch covers most asks.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "get_lab_trend",
                "description": (
                    "Fetch the patient's historical Observation values for a "
                    "single lab, identified by LOINC code (e.g. '4548-4') or "
                    "free text (e.g. 'hemoglobin a1c'). Use only when the "
                    "cached chart context does not already carry enough "
                    "history to answer the user's question."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code_or_text": {
                            "type": "string",
                            "description": "LOINC code or free-text lab name.",
                        },
                        "since": {
                            "type": "string",
                            "description": (
                                "Optional ISO date (YYYY-MM-DD); only "
                                "observations on or after this date are "
                                "returned."
                            ),
                        },
                    },
                    "required": ["code_or_text"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def _chat_system_prompt(allowed_types: frozenset[BriefItemType]) -> str:
    ordered = [t for t in BriefItemType if t in allowed_types]
    type_table_lines = "\n".join(
        f"  - {t.value}: {sorted(ALLOWED_TABLES_FOR_TYPE[t])}" for t in ordered
    )
    type_enum = ", ".join(t.value for t in ordered)
    return (
        "You are an OpenEMR chart-context chat assistant. A physician is "
        "asking follow-up questions about a single patient's chart. Answer "
        "concisely and conversationally, but every factual claim MUST be "
        "grounded in the provided CONTEXT or in tool results.\n"
        "\n"
        "Output JSON conforming to ChatTurn:\n"
        '  { "narrative": "...prose for the physician...",\n'
        '    "facts": [BriefItem, ...] }\n'
        "\n"
        "narrative rules:\n"
        "- Plain English, 1-4 sentences. May reference fact cards with "
        '  "[^N]" footnotes where N matches a fact\'s "anchor".\n'
        "- Every number and ISO date you write MUST also appear verbatim "
        "  in some facts[].verbatim_excerpts. If you cannot ground a number, "
        "  do not write it.\n"
        "- Do NOT advise. No 'I recommend', 'you should', 'consider "
        "  stopping/starting', 'rule out', 'likely has', 'probably', 'might "
        "  want to'. Summarize what is in the chart, not what to do.\n"
        "- If the chart does not answer the question, say so plainly.\n"
        "\n"
        "facts[] rules (each fact is independently verified):\n"
        "1. Every fact MUST cite at least one resource from CONTEXT or tool "
        "   output by exact id.\n"
        "2. Allowed claim types: " + type_enum + ".\n"
        "3. Each claim type may only cite from these resource types:\n"
        f"{type_table_lines}\n"
        "4. Numbers and dates in fact.text MUST appear verbatim in the "
        "   cited rows. Do not paraphrase numbers.\n"
        "5. Set fact.anchor to a small positive integer if the narrative "
        "   references it via [^N].\n"
        "\n"
        "tool use:\n"
        "- The first user message contains the cached chart context.\n"
        "- If the cached context does not have enough history to answer, "
        "  call the appropriate tool. Do not call tools when the answer is "
        "  already in CONTEXT.\n"
        "- After tool results arrive, re-emit the full ChatTurn envelope.\n"
    )


def _chart_context_prompt(
    patient_uuid: str, tool_rows: list[TypedRow]
) -> str:
    if not tool_rows:
        return f"PATIENT: {patient_uuid}\nCONTEXT: (no rows)\n"
    context_lines = []
    for row in tool_rows:
        note = _note_for_row(row)
        suffix = f" [{note}]" if note else ""
        context_lines.append(f"- {row.resource_type}/{row.resource_id}{suffix}")
    return (
        f"PATIENT: {patient_uuid}\n\n"
        "CONTEXT (cite by exact ResourceType/id):\n"
        + "\n".join(context_lines)
        + "\n\nROW DETAILS:\n"
        + "\n".join(_detail_for_row(row) for row in tool_rows)
        + "\n"
    )


def _fact_schema(ordered_types: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ordered_types},
            "text": {"type": "string"},
            "verbatim_excerpts": {"type": "array", "items": {"type": "string"}},
            "citations": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "resource_type": {"type": "string"},
                        "resource_id": {"type": "string"},
                    },
                    "required": ["resource_type", "resource_id"],
                    "additionalProperties": False,
                },
            },
            "anchor": {"type": ["integer", "null"]},
        },
        "required": ["type", "text", "citations"],
        "additionalProperties": False,
    }
