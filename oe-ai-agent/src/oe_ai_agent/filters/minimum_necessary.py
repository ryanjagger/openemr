"""Per-tool field whitelist applied to FHIR responses.

Whitelists are declared statically next to each tool (see ``tools.*``).
This module owns the application logic and the registry of whitelists keyed
by tool name. The filter executes *before* the LLM sees data — for HIPAA
Path 2 it is the seam that satisfies §164.502(b) on outbound payloads.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# Top-level keys per resource type that are allowed through to the model.
TOOL_FIELD_WHITELIST: dict[str, tuple[str, ...]] = {
    "get_demographics": ("name", "birthDate", "gender"),
    "get_active_problems": ("code", "recordedDate", "clinicalStatus"),
    "get_active_medications": (
        "medicationCodeableConcept",
        "dosageInstruction",
        "authoredOn",
    ),
    "get_allergies": ("code", "reaction", "criticality"),
    "get_recent_encounters": ("period", "type", "reasonCode", "participant"),
    "get_recent_observations": (
        "code",
        "valueQuantity",
        "effectiveDateTime",
        "interpretation",
    ),
    "get_lab_trend": (
        "code",
        "valueQuantity",
        "effectiveDateTime",
        "interpretation",
        "referenceRange",
    ),
    "get_recent_notes": ("description", "date", "author", "content"),
}


def filter_fields(tool_name: str, resource: dict[str, Any]) -> dict[str, Any]:
    if tool_name not in TOOL_FIELD_WHITELIST:
        raise KeyError(f"No whitelist registered for tool '{tool_name}'")
    allowed = TOOL_FIELD_WHITELIST[tool_name]
    return {key: resource[key] for key in allowed if key in resource}


def whitelisted_keys(tool_name: str) -> Iterable[str]:
    return TOOL_FIELD_WHITELIST[tool_name]
