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
from oe_ai_agent.schemas.chat import ChatFactType, ChatMessage
from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.verifier.constraints import CHAT_ALLOWED_TABLES_FOR_TYPE


def build_chat_messages(
    *,
    patient_uuid: str,
    cached_context: list[TypedRow],
    history: list[ChatMessage],
    allowed_types: frozenset[ChatFactType],
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
    allowed_types: frozenset[ChatFactType],
) -> dict[str, Any]:
    """Pinned JSON schema for the chat envelope: narrative + facts."""
    ordered = [t.value for t in ChatFactType if t in allowed_types]
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


def _chat_system_prompt(allowed_types: frozenset[ChatFactType]) -> str:
    ordered = [t for t in ChatFactType if t in allowed_types]
    type_table_lines = "\n".join(
        f"  - {t.value}: {sorted(CHAT_ALLOWED_TABLES_FOR_TYPE[t])}" for t in ordered
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
        "- If asked what changed or what to pay attention to, prioritize "
        "  evidence-backed changes, abnormal document findings, intake-form "
        "  deltas, and chart/document discrepancies. Do not issue diagnoses, "
        "  treatment orders, or medication-change instructions.\n"
        "- Do NOT promise future actions. No 'let me pull', 'I'll fetch', "
        "  'one moment', 'checking now', 'give me a sec'. Each turn is "
        "  self-contained: if you need data, call the tool in this same "
        "  turn — the physician does not see your intermediate thinking and "
        "  cannot wait for a follow-up. Either return the answer with the "
        "  data, or say plainly that the chart does not contain it.\n"
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
        "6. Patient-reported answers from indexed intake forms must use "
        "   type='intake_answer' and cite IndexedDocumentFact rows. Do not "
        "   relabel those answers as medication, allergy, demographics, or "
        "   problem facts unless you also cite the corresponding structured "
        "   chart resource from a chart tool.\n"
        "\n"
        "tool use:\n"
        "- The first user message contains cached chart context from earlier "
        "  turns, if any. It may be empty at the start of a conversation.\n"
        "- If the cached context does not have enough history to answer, "
        "  call the appropriate tool. Do not call tools when the answer is "
        "  already in CONTEXT.\n"
        "- Tool selection guide: demographic questions require get_demographics; "
        "  active problem questions require get_active_problems; allergy "
        "  questions require get_allergies; current medication questions "
        "  require get_active_medications; medication history questions "
        "  require get_medication_history; encounter/visit questions require "
        "  get_recent_encounters; note questions require get_recent_notes; "
        "  immunization/vaccine/vaccination questions require "
        "  get_immunizations; structured lab trend questions require "
        "  get_lab_trend; uploaded/indexed lab report questions require "
        "  get_indexed_lab_results; uploaded/indexed intake-form questions "
        "  require get_indexed_intake_answers; uploaded document searches "
        "  require search_indexed_documents or search_indexed_document_facts; "
        "  broader lab/vital/observation questions require get_observations; "
        "  order questions require get_orders; procedure questions require "
        "  get_procedures; appointment questions require get_appointments "
        "  with since when the user asks for upcoming, recent, or date-bound "
        "  appointments; "
        "  care-plan or goal questions require get_care_plan_goals.\n"
        "- Indexed uploaded documents may appear in CONTEXT as DocumentReference "
        "  manifest rows with fields.source='indexed_document_manifest'. "
        "  Manifest rows prove a document exists but are not enough for "
        "  detailed clinical claims; call indexed document tools to retrieve "
        "  facts and source snippets.\n"
        "- Indexed document tool results use IndexedDocumentFact rows. For "
        "  fact_type='intake_answer', say the patient reported it on the "
        "  intake form and use type='intake_answer'. For future indexed fact "
        "  types without a more specific allowed type, use type='document_fact'.\n"
        "- For indexed document facts, copy dates and numbers exactly as they "
        "  appear in the cited row or source snippet. Do not normalize a date "
        "  like 8/14/1967 into 1967-08-14 unless that exact ISO date is in "
        "  the cited row.\n"
        "- If the user asks what changed, what to pay attention to, or what "
        "  evidence supports a finding, use indexed document fact tools when "
        "  uploaded document manifests are relevant. Combine get_lab_trend "
        "  with get_indexed_lab_results when comparing structured chart labs "
        "  against uploaded lab reports.\n"
        "- If the user asks for a list of records from a category absent from "
        "  CONTEXT, call that category's tool before answering.\n"
        "- After tool results arrive, re-emit the full ChatTurn envelope.\n"
    )


def _chart_context_prompt(patient_uuid: str, tool_rows: list[TypedRow]) -> str:
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
