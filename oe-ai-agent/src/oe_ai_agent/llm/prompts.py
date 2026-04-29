"""Prompt construction for the LLM call.

The system prompt encodes the closed-type enum, the citation-required
instruction, the advisory denylist reminder, and the per-claim-type table
constraints. Tool results are serialized into a CONTEXT block keyed by
``ResourceType/resource_id`` so the model can cite by id directly.
"""

from __future__ import annotations

import json
from typing import Any

from oe_ai_agent.schemas.brief import BriefItemType
from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.verifier.constraints import ALLOWED_TABLES_FOR_TYPE


def build_messages(
    patient_uuid: str,
    tool_rows: list[TypedRow],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _system_prompt()},
        {
            "role": "user",
            "content": _user_prompt(patient_uuid=patient_uuid, tool_rows=tool_rows),
        },
    ]


def response_format() -> dict[str, Any]:
    """Pinned JSON schema the LLM must conform to.

    Wired into LiteLLM's ``response_format={"type": "json_schema", ...}`` in
    Phase 3b. The mock client ignores the value but receives it for parity.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "BriefResponse",
            "schema": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": _brief_item_schema(),
                    },
                },
                "required": ["items"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }


def _system_prompt() -> str:
    type_table_lines = "\n".join(
        f"  - {t.value}: {sorted(ALLOWED_TABLES_FOR_TYPE[t])}"
        for t in BriefItemType
    )
    type_enum = ", ".join(t.value for t in BriefItemType)
    return (
        "You are an OpenEMR chart-summary agent. You produce a short, "
        "verifiable brief for a physician about to walk into a patient room.\n"
        "\n"
        "Rules — these are checked downstream and items that violate them "
        "are silently dropped:\n"
        "1. Every item MUST cite at least one resource from the provided "
        "CONTEXT block by its exact id.\n"
        "2. Use only these claim types: " + type_enum + ".\n"
        "3. Each claim type may only cite from these resource types:\n"
        f"{type_table_lines}\n"
        "4. Do NOT advise. Do not write 'I recommend', 'you should', "
        "'consider stopping', 'consider starting', 'rule out', 'likely has', "
        "'probably', or 'might want to'. Summarize what is in the chart, "
        "not what to do about it.\n"
        "5. Numbers and dates in your text MUST appear verbatim somewhere in "
        "the cited rows. Do not paraphrase numbers.\n"
        "6. Output JSON conforming to BriefResponse: "
        '{ "items": [ {"type": "...", "text": "...", '
        '"verbatim_excerpts": [...], "citations": [...]} ] }.'
    )


def _user_prompt(patient_uuid: str, tool_rows: list[TypedRow]) -> str:
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


def _note_for_row(row: TypedRow) -> str:
    """One-line label for the CONTEXT list — used by the synthesizing mock too."""
    fields = row.fields
    if "code" in fields:
        text = _coding_text(fields["code"])
        if text:
            return text
    if "medicationCodeableConcept" in fields:
        text = _coding_text(fields["medicationCodeableConcept"])
        if text:
            return text
    if "name" in fields and isinstance(fields["name"], list) and fields["name"]:
        first = fields["name"][0]
        if isinstance(first, dict):
            text = first.get("text")
            if isinstance(text, str):
                return text
    if row.verbatim_excerpt:
        return row.verbatim_excerpt
    return ""


def _detail_for_row(row: TypedRow) -> str:
    payload: dict[str, Any] = {
        "id": f"{row.resource_type}/{row.resource_id}",
        "patient_id": row.patient_id,
        "last_updated": row.last_updated.isoformat(),
        "fields": row.fields,
    }
    if row.verbatim_excerpt:
        payload["verbatim_excerpt"] = row.verbatim_excerpt
    return json.dumps(payload, default=str)


def _coding_text(value: object) -> str | None:
    candidates: list[dict[str, Any]] = []
    if isinstance(value, dict):
        candidates = [value]
    elif isinstance(value, list):
        candidates = [v for v in value if isinstance(v, dict)]
    for candidate in candidates:
        text = candidate.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        codings = candidate.get("coding")
        if isinstance(codings, list):
            for coding in codings:
                if isinstance(coding, dict):
                    display = coding.get("display")
                    if isinstance(display, str) and display.strip():
                        return display.strip()
    return None


def _brief_item_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": [t.value for t in BriefItemType]},
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
        },
        "required": ["type", "text", "citations"],
        "additionalProperties": False,
    }
