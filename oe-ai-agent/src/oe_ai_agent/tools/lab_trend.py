"""Tool: pull a lab trend by LOINC code or text match.

Drill-down tool exposed to the chat agent. The model invokes it when the
turn-1 pre-fetch (``get_recent_observations``) doesn't carry enough
history to answer a follow-up like "has her A1c trend improved?".

Returns ``TypedRow[]`` in the same envelope every other tool produces, so
the verifier and citation pool treat it identically.

Free-text queries are filtered client-side. OpenEMR's FHIR Observation
endpoint exposes ``code`` as a TOKEN parameter without a working
``:text`` modifier, so we fetch all labs in the window and match
``code.coding.code|display`` and AI-extracted ``code.text`` against the
query substring ourselves.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.tools._common import bundle_resources, extract_ai_provenance, to_typed_row
from oe_ai_agent.tools.fhir_client import FhirClient

TOOL_NAME = "get_lab_trend"
DEFAULT_LIMIT = 50
_FETCH_CAP = 200
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
    label (e.g. ``"hemoglobin a1c"``). LOINC codes are passed to FHIR via
    the ``code`` token parameter; free-text is matched client-side because
    OpenEMR's FHIR endpoint does not support the ``:text`` modifier.
    """
    params: dict[str, str | int] = {
        "patient": patient_uuid,
        "category": "laboratory",
        "_sort": "-date",
    }
    is_loinc = bool(_LOINC_RE.match(code_or_text))
    if is_loinc:
        params["code"] = f"http://loinc.org|{code_or_text}"
        params["_count"] = limit
    else:
        # Pull a wider window when filtering client-side so we don't
        # truncate matches behind unrelated labs.
        params["_count"] = _FETCH_CAP
    if since is not None:
        params["date"] = f"ge{since.isoformat()}"

    bundle = await client.search("Observation", params=params)
    needle = code_or_text.casefold().strip()
    rows: list[TypedRow] = []
    for resource in bundle_resources(bundle):
        if not is_loinc and not _resource_matches_text(resource, needle):
            continue
        provenance = extract_ai_provenance(resource)
        if provenance is not None:
            resource["aiProvenance"] = provenance
        rows.append(to_typed_row(TOOL_NAME, resource, patient_uuid))
        if len(rows) >= limit:
            break
    return rows


def _resource_matches_text(resource: dict[str, Any], needle: str) -> bool:
    code = resource.get("code")
    if not isinstance(code, dict):
        return False
    text = code.get("text")
    if isinstance(text, str) and needle in text.casefold():
        return True
    coding = code.get("coding")
    if not isinstance(coding, list):
        return False
    for entry in coding:
        if not isinstance(entry, dict):
            continue
        for key in ("display", "code"):
            value = entry.get(key)
            if isinstance(value, str) and needle in value.casefold():
                return True
    return False

