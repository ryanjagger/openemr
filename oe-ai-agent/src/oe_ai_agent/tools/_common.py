"""Shared helpers used by every tool to convert FHIR Bundle entries into TypedRow."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from oe_ai_agent.filters.minimum_necessary import filter_fields
from oe_ai_agent.schemas.tool_results import TypedRow


def bundle_resources(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    entries = bundle.get("entry", [])
    return [
        entry["resource"]
        for entry in entries
        if isinstance(entry, dict) and "resource" in entry
    ]


def _patient_id_from_resource(resource: dict[str, Any], patient_uuid: str) -> str:
    """For Patient itself, the id is the patient. For others, walk subject/patient refs."""
    if resource.get("resourceType") == "Patient":
        return str(resource.get("id", patient_uuid))
    for ref_field in ("subject", "patient"):
        ref = resource.get(ref_field)
        if isinstance(ref, dict):
            reference = ref.get("reference")
            if isinstance(reference, str) and reference.startswith("Patient/"):
                return reference.removeprefix("Patient/")
    return patient_uuid


def _last_updated(resource: dict[str, Any]) -> datetime:
    raw = resource.get("meta", {}).get("lastUpdated")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(tz=UTC)


def to_typed_row(
    tool_name: str,
    resource: dict[str, Any],
    patient_uuid: str,
    *,
    verbatim_excerpt: str | None = None,
) -> TypedRow:
    return TypedRow(
        resource_type=str(resource.get("resourceType", "")),
        resource_id=str(resource.get("id", "")),
        patient_id=_patient_id_from_resource(resource, patient_uuid),
        last_updated=_last_updated(resource),
        fields=filter_fields(tool_name, resource),
        verbatim_excerpt=verbatim_excerpt,
    )

