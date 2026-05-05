"""Tools for indexed OpenEMR uploaded-document facts."""

from __future__ import annotations

from typing import Any

from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.tools.fhir_client import FhirClient


async def search_indexed_documents(
    client: FhirClient,
    patient_uuid: str,
    *,
    document_type: str | None = None,
    query: str | None = None,
    limit: int = 25,
) -> list[TypedRow]:
    """Return lightweight manifests for completed ingested documents."""

    payload = await client.api_get(
        f"ai/documents/indexed/{patient_uuid}/document",
        params=_clean_params(
            {
                "document_type": document_type,
                "query": query,
                "limit": limit,
            }
        ),
    )
    documents = payload.get("documents")
    if not isinstance(documents, list):
        return []

    rows: list[TypedRow] = []
    for document in documents:
        if isinstance(document, dict):
            rows.append(_manifest_row(document, patient_uuid))
    return rows


async def search_indexed_document_facts(
    client: FhirClient,
    patient_uuid: str,
    *,
    document_uuid: str | None = None,
    document_type: str | None = None,
    fact_type: str | None = None,
    query: str | None = None,
    observed_on_from: str | None = None,
    observed_on_to: str | None = None,
    limit: int = 50,
) -> list[TypedRow]:
    """Search extracted facts from completed ingested documents."""

    payload = await client.api_get(
        f"ai/documents/indexed-facts/{patient_uuid}/document",
        params=_clean_params(
            {
                "document_uuid": document_uuid,
                "document_type": document_type,
                "fact_type": fact_type,
                "query": query,
                "observed_on_from": observed_on_from,
                "observed_on_to": observed_on_to,
                "limit": limit,
            }
        ),
    )
    facts = payload.get("facts")
    if not isinstance(facts, list):
        return []

    rows: list[TypedRow] = []
    for fact in facts:
        if isinstance(fact, dict):
            row_payload = dict(fact)
            if row_payload.get("resource_type") == "DocumentReference":
                fields = row_payload.get("fields")
                if isinstance(fields, dict) and fields.get("source") == "indexed_document_fact":
                    row_payload["resource_type"] = "IndexedDocumentFact"
            rows.append(TypedRow.model_validate(row_payload))
    return rows


async def get_indexed_lab_results(
    client: FhirClient,
    patient_uuid: str,
    *,
    code_or_text: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> list[TypedRow]:
    """Return lab_result facts extracted from uploaded/indexed documents."""

    return await search_indexed_document_facts(
        client,
        patient_uuid,
        document_type="lab_report",
        fact_type="lab_result",
        query=code_or_text,
        observed_on_from=since,
        limit=limit,
    )


async def get_indexed_intake_answers(
    client: FhirClient,
    patient_uuid: str,
    *,
    query: str | None = None,
    limit: int = 50,
) -> list[TypedRow]:
    """Return intake_answer facts extracted from uploaded/indexed intake forms."""

    return await search_indexed_document_facts(
        client,
        patient_uuid,
        document_type="intake_form",
        fact_type="intake_answer",
        query=query,
        limit=limit,
    )


def _manifest_row(document: dict[str, Any], patient_uuid: str) -> TypedRow:
    document_uuid = str(document.get("document_uuid") or document.get("uuid") or "")
    return TypedRow.model_validate(
        {
            "resource_type": "DocumentReference",
            "resource_id": document_uuid,
            "patient_id": patient_uuid,
            "last_updated": document.get("last_updated"),
            "fields": {
                "source": "indexed_document_manifest",
                "document_uuid": document_uuid,
                "document_type": document.get("document_type"),
                "filename": document.get("filename"),
                "mimetype": document.get("mimetype"),
                "docdate": document.get("docdate"),
                "model_id": document.get("model_id"),
                "fact_count": document.get("fact_count"),
                "document_summary": document.get("document_summary"),
                "extraction_confidence": document.get("extraction_confidence"),
            },
            "verbatim_excerpt": document.get("document_summary"),
        }
    )


def _clean_params(values: dict[str, str | int | None]) -> dict[str, str | int]:
    return {
        key: value
        for key, value in values.items()
        if value is not None and value != ""
    }
