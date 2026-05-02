"""Chat tool: search patient Appointments."""

from __future__ import annotations

from datetime import date

from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.tools._common import bundle_resources, to_typed_row
from oe_ai_agent.tools.fhir_client import FhirClient

TOOL_NAME = "get_appointments"
DEFAULT_LIMIT = 25


async def get_appointments(
    client: FhirClient,
    patient_uuid: str,
    *,
    since: date | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[TypedRow]:
    params: dict[str, str | int] = {
        "patient": patient_uuid,
        "_count": limit,
        "_sort": "-date",
    }
    if since is not None:
        params["date"] = f"ge{since.isoformat()}"

    bundle = await client.search(
        "Appointment",
        params=params,
    )
    return [to_typed_row(TOOL_NAME, r, patient_uuid) for r in bundle_resources(bundle)]
