"""Chat tool: fetch FHIR QuestionnaireResponse resources for a patient.

Replaces ``get_indexed_intake_answers``. AI-extracted intake forms now land in
the native ``questionnaire_response`` table and surface via FHIR; the
``ai_questionnaire_response_provenance`` rows are surfaced as a nested
extension on each matching ``item[]`` entry. We pre-flatten that extension
into a top-level ``aiProvenance`` field per item so the LLM does not need
to walk FHIR's extension nesting to cite source page/bbox/snippet.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.tools._common import (
    bundle_resources,
    extract_ai_provenance,
    to_typed_row,
)
from oe_ai_agent.tools.fhir_client import FhirClient

TOOL_NAME = "get_questionnaire_responses"
DEFAULT_LIMIT = 25


async def get_questionnaire_responses(
    client: FhirClient,
    patient_uuid: str,
    *,
    since: date | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[TypedRow]:
    """Search QuestionnaireResponse history (clinician-entered AND AI-extracted)."""
    params: dict[str, str | int] = {
        "patient": patient_uuid,
        "_count": limit,
        "_sort": "-authored",
    }
    if since is not None:
        params["authored"] = f"ge{since.isoformat()}"

    bundle = await client.search("QuestionnaireResponse", params=params)
    rows: list[TypedRow] = []
    for resource in bundle_resources(bundle):
        _flatten_item_provenance(resource.get("item"))
        rows.append(to_typed_row(TOOL_NAME, resource, patient_uuid))
    return rows


def _flatten_item_provenance(items: Any) -> None:
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        provenance = extract_ai_provenance(item)
        if provenance is not None:
            item["aiProvenance"] = provenance
            # The raw FHIR extension is now redundant — drop it so the
            # whitelist passes a clean shape to the LLM.
            item.pop("extension", None)
        nested = item.get("item")
        if isinstance(nested, list):
            _flatten_item_provenance(nested)
