"""Tool: list active MedicationRequests."""

from __future__ import annotations

from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.tools._common import bundle_resources, to_typed_row
from oe_ai_agent.tools.fhir_client import FhirClient

TOOL_NAME = "get_active_medications"


async def get_active_medications(client: FhirClient, patient_uuid: str) -> list[TypedRow]:
    bundle = await client.search(
        "MedicationRequest",
        params={"patient": patient_uuid, "status": "active"},
    )
    return [to_typed_row(TOOL_NAME, r, patient_uuid) for r in bundle_resources(bundle)]

