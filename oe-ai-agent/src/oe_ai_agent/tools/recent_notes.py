"""Tool: recent clinical-note DocumentReferences."""

from __future__ import annotations

from typing import Any

from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.tools._common import bundle_resources, to_typed_row
from oe_ai_agent.tools.fhir_client import FhirClient

TOOL_NAME = "get_recent_notes"
DEFAULT_LIMIT = 3


async def get_recent_notes(
    client: FhirClient,
    patient_uuid: str,
    limit: int = DEFAULT_LIMIT,
) -> list[TypedRow]:
    bundle = await client.search(
        "DocumentReference",
        params={"patient": patient_uuid, "_count": limit, "_sort": "-date"},
    )
    return [
        to_typed_row(
            TOOL_NAME,
            resource,
            patient_uuid,
            verbatim_excerpt=_excerpt(resource),
        )
        for resource in bundle_resources(bundle)
    ]


def _excerpt(resource: dict[str, Any]) -> str | None:
    description = resource.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    contents = resource.get("content")
    if isinstance(contents, list):
        for entry in contents:
            attachment = entry.get("attachment") if isinstance(entry, dict) else None
            if isinstance(attachment, dict):
                title = attachment.get("title")
                if isinstance(title, str) and title.strip():
                    return title.strip()
    return None

