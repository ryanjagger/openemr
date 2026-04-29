"""Tool: most recent Encounters."""

from __future__ import annotations

from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.tools._common import bundle_resources, to_typed_row
from oe_ai_agent.tools.fhir_client import FhirClient

TOOL_NAME = "get_recent_encounters"
DEFAULT_LIMIT = 5


async def get_recent_encounters(
    client: FhirClient,
    patient_uuid: str,
    limit: int = DEFAULT_LIMIT,
) -> list[TypedRow]:
    bundle = await client.search(
        "Encounter",
        params={"patient": patient_uuid, "_count": limit, "_sort": "-date"},
    )
    return [to_typed_row(TOOL_NAME, r, patient_uuid) for r in bundle_resources(bundle)]
