"""Chat tool: search patient Observations by category, code/text, and date."""

from __future__ import annotations

import re
from datetime import date

from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.tools._common import bundle_resources, to_typed_row
from oe_ai_agent.tools.fhir_client import FhirClient

TOOL_NAME = "get_observations"
DEFAULT_LIMIT = 50
_LOINC_RE = re.compile(r"^\d{1,5}-\d$")


async def get_observations(
    client: FhirClient,
    patient_uuid: str,
    *,
    category: str | None = None,
    code_or_text: str | None = None,
    since: date | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[TypedRow]:
    params: dict[str, str | int] = {
        "patient": patient_uuid,
        "_count": limit,
        "_sort": "-date",
    }
    if category:
        params["category"] = category
    if code_or_text:
        if _LOINC_RE.match(code_or_text):
            params["code"] = f"http://loinc.org|{code_or_text}"
        else:
            params["code:text"] = code_or_text
    if since is not None:
        params["date"] = f"ge{since.isoformat()}"

    bundle = await client.search("Observation", params=params)
    return [to_typed_row(TOOL_NAME, r, patient_uuid) for r in bundle_resources(bundle)]

