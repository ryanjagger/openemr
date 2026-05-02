"""Chat tool: search patient CarePlan and Goal resources."""

from __future__ import annotations

import asyncio
from datetime import date

from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.tools._common import bundle_resources, to_typed_row
from oe_ai_agent.tools.fhir_client import FhirClient

TOOL_NAME = "get_care_plan_goals"
DEFAULT_LIMIT = 25


async def get_care_plan_goals(
    client: FhirClient,
    patient_uuid: str,
    *,
    category: str | None = None,
    since: date | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[TypedRow]:
    care_plan_params: dict[str, str | int] = {
        "patient": patient_uuid,
        "_count": limit,
    }
    if category:
        care_plan_params["category"] = category
    if since is not None:
        care_plan_params["_lastUpdated"] = f"ge{since.isoformat()}"

    goal_params: dict[str, str | int] = {
        "patient": patient_uuid,
        "_count": limit,
    }
    if since is not None:
        goal_params["_lastUpdated"] = f"ge{since.isoformat()}"

    care_plan_bundle, goal_bundle = await asyncio.gather(
        client.search("CarePlan", params=care_plan_params),
        client.search("Goal", params=goal_params),
    )

    return [
        to_typed_row(TOOL_NAME, resource, patient_uuid)
        for bundle in (care_plan_bundle, goal_bundle)
        for resource in bundle_resources(bundle)
    ]
