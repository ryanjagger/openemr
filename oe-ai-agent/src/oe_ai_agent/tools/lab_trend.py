"""Tool: pull a lab trend by LOINC code or text match.

Drill-down tool exposed to the chat agent. The model invokes it when the
turn-1 pre-fetch (``get_recent_observations``) doesn't carry enough
history to answer a follow-up like "has her A1c trend improved?".

Returns ``TypedRow[]`` in the same envelope every other tool produces, so
the verifier and citation pool treat it identically.
"""

from __future__ import annotations

import re
from datetime import date

from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.tools._common import bundle_resources, to_typed_row
from oe_ai_agent.tools.fhir_client import FhirClient

TOOL_NAME = "get_lab_trend"
DEFAULT_LIMIT = 50
_LOINC_RE = re.compile(r"^\d{1,5}-\d$")


async def get_lab_trend(
    client: FhirClient,
    patient_uuid: str,
    *,
    code_or_text: str,
    since: date | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[TypedRow]:
    """Search Observation history for a single lab.

    ``code_or_text`` may be a LOINC code (e.g. ``"4548-4"``) or a free-text
    label (e.g. ``"hemoglobin a1c"``). Free-text queries use FHIR's
    ``code:text`` search modifier.
    """
    params: dict[str, str | int] = {
        "patient": patient_uuid,
        "category": "laboratory",
        "_count": limit,
        "_sort": "-date",
    }
    if _LOINC_RE.match(code_or_text):
        params["code"] = f"http://loinc.org|{code_or_text}"
    else:
        params["code:text"] = code_or_text
    if since is not None:
        params["date"] = f"ge{since.isoformat()}"

    bundle = await client.search("Observation", params=params)
    return [to_typed_row(TOOL_NAME, r, patient_uuid) for r in bundle_resources(bundle)]

