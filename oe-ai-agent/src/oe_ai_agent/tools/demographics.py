"""Tool: read the Patient resource."""

from __future__ import annotations

from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.tools._common import to_typed_row
from oe_ai_agent.tools.fhir_client import FhirClient

TOOL_NAME = "get_demographics"


async def get_demographics(client: FhirClient, patient_uuid: str) -> list[TypedRow]:
    resource = await client.read("Patient", patient_uuid)
    return [to_typed_row(TOOL_NAME, resource, patient_uuid)]
