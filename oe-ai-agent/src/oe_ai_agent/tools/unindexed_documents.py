"""Tools for the supervisor's extractor worker.

Two tools:

* ``list_unindexed_documents`` — return uploaded documents that have not yet
  been ingested. Wraps ``GET /api/ai/documents/recent/:pid`` and filters out
  rows that already carry ``already_ingested = true``.
* ``extract_documents`` — kick off ingestion for a list of document UUIDs
  and block until the job completes (or a timeout fires). Wraps
  ``POST /api/ai/documents/ingest/:pid`` and polls
  ``GET /api/ai/documents/:pid/jobs/:jobId``. Returns no rows directly —
  post-Phase-5 the extracted clinical data lives in FHIR Observation
  (lab_report) and FHIR QuestionnaireResponse (intake_form). The supervisor
  routes to evidence_retriever afterwards, which calls
  ``get_lab_trend`` / ``get_observations`` / ``get_questionnaire_responses``
  to surface the just-ingested rows for citation.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.schemas.unindexed_document import UnindexedDocument
from oe_ai_agent.tools.fhir_client import FhirClient, FhirError

DEFAULT_POLL_INTERVAL_SECONDS = 1.5
DEFAULT_POLL_TIMEOUT_SECONDS = 60.0
TERMINAL_JOB_STATUSES = frozenset({"completed", "partial", "failed"})


async def list_unindexed_documents(
    client: FhirClient,
    patient_uuid: str,
) -> list[UnindexedDocument]:
    payload = await client.api_get(f"ai/documents/recent/{patient_uuid}")
    documents = payload.get("documents")
    if not isinstance(documents, list):
        return []

    results: list[UnindexedDocument] = []
    for entry in documents:
        if not isinstance(entry, dict):
            continue
        if entry.get("already_ingested") is True:
            continue
        document_id = entry.get("id")
        if not isinstance(document_id, int):
            continue
        document_uuid = entry.get("uuid")
        if not isinstance(document_uuid, str) or not document_uuid:
            continue
        filename = str(entry.get("filename") or "")
        mimetype = str(entry.get("mimetype") or "")
        results.append(
            UnindexedDocument(
                document_id=document_id,
                document_uuid=document_uuid,
                filename=filename,
                mimetype=mimetype,
                docdate=_optional_str(entry.get("docdate")),
                category_name=_optional_str(entry.get("category_name")),
                inferred_document_type=_infer_document_type(
                    filename, _optional_str(entry.get("category_name"))
                ),
            )
        )
    return results


async def extract_documents(
    client: FhirClient,
    patient_uuid: str,
    *,
    selections: list[dict[str, str | int]],
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    poll_timeout_seconds: float = DEFAULT_POLL_TIMEOUT_SECONDS,
) -> tuple[list[TypedRow], dict[str, Any]]:
    """Kick off ingestion and block until the job completes.

    Returns ``(rows, status)`` where ``rows`` is always empty (extracted
    clinical data is fetched separately by evidence_retriever via
    ``get_lab_trend`` / ``get_observations`` / ``get_questionnaire_responses``)
    and ``status`` is the final job payload from OpenEMR for trace metadata.
    """
    if not selections:
        return [], {
            "status": "no_selection",
            "document_count": 0,
            "processed_count": 0,
            "failed_count": 0,
        }

    job = await client.api_post(
        f"ai/documents/ingest/{patient_uuid}",
        {"documents": selections},
    )
    job_uuid = _job_uuid(job)
    if job_uuid is None:
        raise FhirError(
            "OpenEMR ingestion endpoint did not return a job_id",
            status_code=None,
        )

    final_job = await _poll_until_terminal(
        client,
        patient_uuid=patient_uuid,
        job_uuid=job_uuid,
        poll_interval_seconds=poll_interval_seconds,
        poll_timeout_seconds=poll_timeout_seconds,
    )

    # Post-Phase-5: extracted facts now live in FHIR Observation (labs) and
    # FHIR QuestionnaireResponse (intake), not in the legacy indexed-facts
    # API. We do not echo rows back here; the supervisor routes to
    # evidence_retriever next, which calls get_lab_trend / get_observations /
    # get_questionnaire_responses to surface the just-ingested data.
    rows: list[TypedRow] = []
    return rows, final_job


async def _poll_until_terminal(
    client: FhirClient,
    *,
    patient_uuid: str,
    job_uuid: str,
    poll_interval_seconds: float,
    poll_timeout_seconds: float,
) -> dict[str, Any]:
    deadline = _monotonic() + poll_timeout_seconds
    while True:
        job = await client.api_get(f"ai/documents/{patient_uuid}/jobs/{job_uuid}")
        status = str(job.get("status") or "")
        if status in TERMINAL_JOB_STATUSES:
            return job
        if _monotonic() >= deadline:
            return {**job, "status": status or "timeout", "timed_out": True}
        await asyncio.sleep(poll_interval_seconds)


def _job_uuid(payload: dict[str, Any]) -> str | None:
    candidate = payload.get("job_id") or payload.get("job_uuid")
    if isinstance(candidate, str) and candidate:
        return candidate
    return None


def _job_documents(job: dict[str, Any]) -> list[dict[str, Any]]:
    documents = job.get("documents")
    if not isinstance(documents, list):
        return []
    return [doc for doc in documents if isinstance(doc, dict)]


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _infer_document_type(filename: str, category_name: str | None) -> str | None:
    """Best-effort hint shown to the supervisor.

    The supervisor uses this only as a routing hint — extraction itself
    always validates the type from the document bytes.
    """
    haystack = f"{filename} {category_name or ''}".lower()
    if any(token in haystack for token in ("intake", "history", "questionnaire", "form")):
        return "intake_form"
    if any(token in haystack for token in ("lab", "labs", "result", "panel")):
        return "lab_report"
    return None


def _monotonic() -> float:
    """Indirection so tests can monkeypatch the clock if needed."""
    return datetime.now(tz=UTC).timestamp()
