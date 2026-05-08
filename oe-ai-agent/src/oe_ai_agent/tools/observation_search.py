"""Chat tool: search patient Observations by category, code/text, and date.

Free-text ``code_or_text`` is filtered client-side. OpenEMR's FHIR
Observation endpoint exposes ``code`` as a TOKEN parameter and does not
support the ``:text`` modifier, so passing the text upstream silently
returns 0 hits.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.tools._common import bundle_resources, extract_ai_provenance, to_typed_row
from oe_ai_agent.tools.fhir_client import FhirClient

TOOL_NAME = "get_observations"
DEFAULT_LIMIT = 50
_FETCH_CAP = 200
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
        "_sort": "-date",
    }
    if category:
        params["category"] = category
    is_loinc = bool(code_or_text and _LOINC_RE.match(code_or_text))
    if is_loinc:
        assert code_or_text is not None
        params["code"] = f"http://loinc.org|{code_or_text}"
        params["_count"] = limit
    elif code_or_text:
        params["_count"] = _FETCH_CAP
    else:
        params["_count"] = limit
    if since is not None:
        params["date"] = f"ge{since.isoformat()}"

    bundle = await client.search("Observation", params=params)
    needle = code_or_text.casefold().strip() if code_or_text and not is_loinc else None
    rows: list[TypedRow] = []
    for resource in bundle_resources(bundle):
        if needle is not None and not _resource_matches_text(resource, needle):
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

