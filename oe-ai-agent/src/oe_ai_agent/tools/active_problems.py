"""Tool: list active Conditions."""

from __future__ import annotations

from typing import Any

from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.tools._common import bundle_resources, to_typed_row
from oe_ai_agent.tools.fhir_client import FhirClient

TOOL_NAME = "get_active_problems"


async def get_active_problems(client: FhirClient, patient_uuid: str) -> list[TypedRow]:
    # The FHIR Condition endpoint cannot search by `clinical-status`: the
    # encounter-diagnosis service rejects it as an unsupported field, and the
    # problem-list-item service maps it to a column that does not exist in
    # SQL (clinical_status is computed in PHP after fetch). Scope to the
    # problem list with `category=problem-list-item` and filter active rows
    # client-side.
    bundle = await client.search(
        "Condition",
        params={"patient": patient_uuid, "category": "problem-list-item"},
    )
    return [
        to_typed_row(TOOL_NAME, r, patient_uuid)
        for r in bundle_resources(bundle)
        if _is_active_condition(r)
    ]


def _is_active_condition(resource: dict[str, Any]) -> bool:
    clinical_status = resource.get("clinicalStatus")
    if not isinstance(clinical_status, dict):
        return True
    codings = clinical_status.get("coding")
    if not isinstance(codings, list):
        return True
    return any(
        isinstance(coding, dict) and coding.get("code") == "active"
        for coding in codings
    )
