"""Registry for model-callable chat tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from oe_ai_agent.llm.client import LlmToolCall
from oe_ai_agent.schemas.tool_results import ToolError, TypedRow
from oe_ai_agent.tools.active_medications import get_active_medications
from oe_ai_agent.tools.active_problems import get_active_problems
from oe_ai_agent.tools.allergies import get_allergies
from oe_ai_agent.tools.appointments import get_appointments
from oe_ai_agent.tools.care_plan_goals import get_care_plan_goals
from oe_ai_agent.tools.clinical_guidelines import search_clinical_guidelines
from oe_ai_agent.tools.demographics import get_demographics
from oe_ai_agent.tools.fhir_client import FhirClient, FhirError
from oe_ai_agent.tools.immunizations import get_immunizations
from oe_ai_agent.tools.lab_trend import get_lab_trend
from oe_ai_agent.tools.medication_history import get_medication_history
from oe_ai_agent.tools.observation_search import get_observations
from oe_ai_agent.tools.orders import get_orders
from oe_ai_agent.tools.procedures import get_procedures
from oe_ai_agent.tools.questionnaire_responses import get_questionnaire_responses
from oe_ai_agent.tools.recent_encounters import get_recent_encounters
from oe_ai_agent.tools.recent_notes import get_recent_notes
from oe_ai_agent.tools.unindexed_documents import (
    extract_documents,
    list_unindexed_documents,
)

ToolHandler = Callable[[FhirClient, str, dict[str, object]], Awaitable[list[TypedRow]]]
DEFAULT_LIMIT = 50
MAX_LIMIT = 100


@dataclass(frozen=True)
class ChatToolSpec:
    name: str
    description: str
    parameters: dict[str, object]
    handler: ToolHandler

    def schema(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


async def _handle_demographics(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    _reject_arguments(arguments)
    return await get_demographics(client, patient_uuid)


async def _handle_active_problems(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    _reject_arguments(arguments)
    return await get_active_problems(client, patient_uuid)


async def _handle_active_medications(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    _reject_arguments(arguments)
    return await get_active_medications(client, patient_uuid)


async def _handle_allergies(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    _reject_arguments(arguments)
    return await get_allergies(client, patient_uuid)


async def _handle_recent_encounters(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    return await get_recent_encounters(
        client,
        patient_uuid,
        limit=_optional_limit(arguments.get("limit")),
    )


async def _handle_recent_notes(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    return await get_recent_notes(
        client,
        patient_uuid,
        limit=_optional_limit(arguments.get("limit")),
    )


async def _handle_questionnaire_responses(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    return await get_questionnaire_responses(
        client,
        patient_uuid,
        since=_optional_iso_date(arguments.get("since"), "since"),
        limit=_optional_limit(arguments.get("limit")),
    )


async def _handle_lab_trend(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    code_or_text = arguments.get("code_or_text")
    if not isinstance(code_or_text, str) or not code_or_text.strip():
        raise ValueError("missing required argument code_or_text")

    since = _optional_iso_date(arguments.get("since"), "since")
    return await get_lab_trend(
        client,
        patient_uuid,
        code_or_text=code_or_text.strip(),
        since=since,
    )


async def _handle_observations(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    return await get_observations(
        client,
        patient_uuid,
        category=_optional_str(arguments.get("category")),
        code_or_text=_optional_str(arguments.get("code_or_text")),
        since=_optional_iso_date(arguments.get("since"), "since"),
        limit=_optional_limit(arguments.get("limit")),
    )


async def _handle_medication_history(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    return await get_medication_history(
        client,
        patient_uuid,
        status=_optional_str(arguments.get("status")),
        since=_optional_iso_date(arguments.get("since"), "since"),
        limit=_optional_limit(arguments.get("limit")),
    )


async def _handle_orders(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    return await get_orders(
        client,
        patient_uuid,
        status=_optional_str(arguments.get("status")),
        category=_optional_str(arguments.get("category")),
        code_or_text=_optional_str(arguments.get("code_or_text")),
        since=_optional_iso_date(arguments.get("since"), "since"),
        limit=_optional_limit(arguments.get("limit")),
    )


async def _handle_procedures(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    return await get_procedures(
        client,
        patient_uuid,
        status=_optional_str(arguments.get("status")),
        code_or_text=_optional_str(arguments.get("code_or_text")),
        since=_optional_iso_date(arguments.get("since"), "since"),
        limit=_optional_limit(arguments.get("limit")),
    )


async def _handle_immunizations(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    return await get_immunizations(
        client,
        patient_uuid,
        status=_optional_str(arguments.get("status")),
        code_or_text=_optional_str(arguments.get("code_or_text")),
        since=_optional_iso_date(arguments.get("since"), "since"),
        limit=_optional_limit(arguments.get("limit")),
    )


async def _handle_appointments(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    return await get_appointments(
        client,
        patient_uuid,
        since=_optional_iso_date(arguments.get("since"), "since"),
        limit=_optional_limit(arguments.get("limit")),
    )


async def _handle_care_plan_goals(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    return await get_care_plan_goals(
        client,
        patient_uuid,
        category=_optional_str(arguments.get("category")),
        since=_optional_iso_date(arguments.get("since"), "since"),
        limit=_optional_limit(arguments.get("limit")),
    )


async def _handle_search_clinical_guidelines(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    del client, patient_uuid
    query = _optional_str(arguments.get("query"))
    if query is None:
        raise ValueError("query is required")
    return await search_clinical_guidelines(
        query=query,
        category=_optional_str(arguments.get("category")),
        topic_tag=_optional_str(arguments.get("topic_tag")),
        limit=_optional_limit(arguments.get("limit")),
    )


async def _handle_list_unindexed_documents(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    _reject_arguments(arguments)
    docs = await list_unindexed_documents(client, patient_uuid)
    rows: list[TypedRow] = []
    moment = datetime.now(tz=UTC)
    for doc in docs:
        rows.append(
            TypedRow(
                resource_type="UnindexedDocument",
                resource_id=doc.document_uuid,
                patient_id=patient_uuid,
                last_updated=moment,
                fields={
                    "source": "unindexed_document_manifest",
                    "document_id": doc.document_id,
                    "document_uuid": doc.document_uuid,
                    "filename": doc.filename,
                    "mimetype": doc.mimetype,
                    "docdate": doc.docdate,
                    "category_name": doc.category_name,
                    "inferred_document_type": doc.inferred_document_type,
                },
            )
        )
    return rows


EXTRACTION_PENDING_SENTINEL = "EXTRACTION_PENDING"


async def _handle_extract_documents(
    client: FhirClient,
    patient_uuid: str,
    arguments: dict[str, object],
) -> list[TypedRow]:
    selections = _parse_extract_selections(arguments.get("documents"))
    if not selections:
        raise ValueError("documents must be a non-empty list")
    rows, status = await extract_documents(
        client,
        patient_uuid,
        selections=selections,
    )
    if status.get("timed_out") is True:
        # Surface as a tool error so the extractor LLM can report it instead
        # of pretending the job finished. The extractor node also keys off
        # this sentinel to flip ChatState.extraction_pending so finalize can
        # tell the user to retry in ~30s.
        raise ValueError(
            f"{EXTRACTION_PENDING_SENTINEL}: document extraction is still "
            "running in the background and rows are not yet visible. Ask "
            "the user to retry in 30 seconds."
        )
    return rows


def _parse_extract_selections(value: object) -> list[dict[str, str | int]]:
    if not isinstance(value, list):
        return []
    selections: list[dict[str, str | int]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        document_id_raw = raw.get("document_id")
        document_type_raw = raw.get("document_type")
        if not isinstance(document_type_raw, str) or not document_type_raw.strip():
            continue
        if document_type_raw not in {"lab_report", "intake_form"}:
            continue
        if isinstance(document_id_raw, int):
            document_id = document_id_raw
        elif isinstance(document_id_raw, str) and document_id_raw.isdigit():
            document_id = int(document_id_raw)
        else:
            continue
        selections.append(
            {"document_id": document_id, "document_type": document_type_raw.strip()}
        )
    return selections


def _reject_arguments(arguments: dict[str, object]) -> None:
    if arguments:
        raise ValueError("this tool does not accept arguments")


def _optional_str(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("expected string argument")
    return value.strip() or None


def _optional_limit(value: object) -> int:
    if value is None or value == "":
        return DEFAULT_LIMIT
    if not isinstance(value, int):
        raise ValueError("limit must be an integer")
    if value < 1 or value > MAX_LIMIT:
        raise ValueError("limit must be between 1 and 100")
    return value


def _optional_iso_date(value: object, name: str) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid {name} {value!r}; expected YYYY-MM-DD") from exc


def _optional_date_string(value: object, name: str) -> str | None:
    parsed = _optional_iso_date(value, name)
    return parsed.isoformat() if parsed is not None else None


def _dated_search_properties(
    *,
    extra: dict[str, dict[str, object]],
) -> dict[str, object]:
    return {
        **extra,
        "since": {
            "type": "string",
            "description": "Optional ISO date (YYYY-MM-DD) lower bound.",
        },
        "limit": {
            "type": "integer",
            "description": "Optional result limit from 1 to 100.",
        },
    }


CHAT_TOOL_REGISTRY: dict[str, ChatToolSpec] = {
    "get_demographics": ChatToolSpec(
        name="get_demographics",
        description=(
            "Read the patient's Patient resource for basic demographics such "
            "as name, birth date, and administrative gender."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=_handle_demographics,
    ),
    "get_active_problems": ChatToolSpec(
        name="get_active_problems",
        description="Fetch the patient's active Condition problem list entries.",
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=_handle_active_problems,
    ),
    "get_active_medications": ChatToolSpec(
        name="get_active_medications",
        description="Fetch the patient's active MedicationRequest entries.",
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=_handle_active_medications,
    ),
    "get_allergies": ChatToolSpec(
        name="get_allergies",
        description="Fetch the patient's AllergyIntolerance entries.",
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=_handle_allergies,
    ),
    "get_recent_encounters": ChatToolSpec(
        name="get_recent_encounters",
        description="Fetch the patient's most recent Encounter records.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Optional result limit from 1 to 100.",
                },
            },
            "additionalProperties": False,
        },
        handler=_handle_recent_encounters,
    ),
    "get_recent_notes": ChatToolSpec(
        name="get_recent_notes",
        description="Fetch the patient's most recent clinical-note DocumentReferences.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Optional result limit from 1 to 100.",
                },
            },
            "additionalProperties": False,
        },
        handler=_handle_recent_notes,
    ),
    "get_questionnaire_responses": ChatToolSpec(
        name="get_questionnaire_responses",
        description=(
            "Fetch FHIR QuestionnaireResponse resources for the patient, "
            "including any AI-extracted intake forms uploaded as PDFs. Each "
            "response carries item[] entries (linkId, text, answer); "
            "AI-extracted items also include an aiProvenance field with "
            "documentId, page, bbox, snippet, confidence, and model for "
            "source citation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "since": {
                    "type": "string",
                    "description": (
                        "Optional ISO date (YYYY-MM-DD); only responses "
                        "authored on or after this date are returned."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Optional result limit from 1 to 100.",
                },
            },
            "additionalProperties": False,
        },
        handler=_handle_questionnaire_responses,
    ),
    "search_clinical_guidelines": ChatToolSpec(
        name="search_clinical_guidelines",
        description=(
            "Search the local clinical guideline corpus using hybrid keyword "
            "and vector retrieval with source snippets. Use for general "
            "clinical guideline, screening, counseling, immunization, "
            "preventive-care, pharmacology, or public-health guidance questions."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Guideline search query. Do not include patient names, "
                        "MRNs, or other direct identifiers."
                    ),
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Optional corpus category filter, such as preventive, "
                        "cardiometabolic, cancer_screening, infectious_disease, "
                        "mental_health_substance, immunizations, or pharmacology."
                    ),
                },
                "topic_tag": {
                    "type": "string",
                    "description": "Optional topic tag filter such as obesity or breast cancer.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Optional result limit from 1 to 10.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=_handle_search_clinical_guidelines,
    ),
    "get_lab_trend": ChatToolSpec(
        name="get_lab_trend",
        description=(
            "Fetch the patient's historical Observation values for a single "
            "lab, identified by LOINC code (e.g. '4548-4') or free text "
            "(e.g. 'hemoglobin a1c'). Includes both clinician-entered / "
            "HL7-vendor results and AI-extracted results from uploaded lab "
            "PDFs (the latter carry an aiProvenance field with document_id, "
            "page, bbox, and snippet for citation). Use only when the cached "
            "chart context does not already carry enough history to answer "
            "the user's question."
        ),
        parameters={
            "type": "object",
            "properties": {
                "code_or_text": {
                    "type": "string",
                    "description": "LOINC code or free-text lab name.",
                },
                "since": {
                    "type": "string",
                    "description": (
                        "Optional ISO date (YYYY-MM-DD); only observations on "
                        "or after this date are returned."
                    ),
                },
            },
            "required": ["code_or_text"],
            "additionalProperties": False,
        },
        handler=_handle_lab_trend,
    ),
    "get_observations": ChatToolSpec(
        name="get_observations",
        description=(
            "Search the patient's Observation history, including labs, vital "
            "signs, social history, and other coded observations. Use category "
            "for broad filters such as 'laboratory' or 'vital-signs'. "
            "Lab results extracted from uploaded PDFs carry an aiProvenance "
            "field (document_id, page, bbox, snippet, confidence, model) for "
            "source citation."
        ),
        parameters={
            "type": "object",
            "properties": _dated_search_properties(
                extra={
                    "category": {
                        "type": "string",
                        "description": (
                            "FHIR observation category such as laboratory or vital-signs."
                        ),
                    },
                    "code_or_text": {
                        "type": "string",
                        "description": "LOINC code or free-text observation name.",
                    },
                }
            ),
            "additionalProperties": False,
        },
        handler=_handle_observations,
    ),
    "get_medication_history": ChatToolSpec(
        name="get_medication_history",
        description=(
            "Search the patient's MedicationRequest history, including active, "
            "stopped, completed, or cancelled medication requests."
        ),
        parameters={
            "type": "object",
            "properties": _dated_search_properties(
                extra={
                    "status": {
                        "type": "string",
                        "description": "Optional MedicationRequest status filter.",
                    },
                }
            ),
            "additionalProperties": False,
        },
        handler=_handle_medication_history,
    ),
    "get_orders": ChatToolSpec(
        name="get_orders",
        description="Search the patient's ServiceRequest orders.",
        parameters={
            "type": "object",
            "properties": _dated_search_properties(
                extra={
                    "status": {"type": "string", "description": "Optional order status."},
                    "category": {"type": "string", "description": "Optional order category."},
                    "code_or_text": {"type": "string", "description": "Free-text order name."},
                }
            ),
            "additionalProperties": False,
        },
        handler=_handle_orders,
    ),
    "get_procedures": ChatToolSpec(
        name="get_procedures",
        description="Search the patient's Procedure history.",
        parameters={
            "type": "object",
            "properties": _dated_search_properties(
                extra={
                    "status": {"type": "string", "description": "Optional procedure status."},
                    "code_or_text": {"type": "string", "description": "Free-text procedure name."},
                }
            ),
            "additionalProperties": False,
        },
        handler=_handle_procedures,
    ),
    "get_immunizations": ChatToolSpec(
        name="get_immunizations",
        description="Search the patient's Immunization history.",
        parameters={
            "type": "object",
            "properties": _dated_search_properties(
                extra={
                    "status": {"type": "string", "description": "Optional immunization status."},
                    "code_or_text": {"type": "string", "description": "Free-text vaccine name."},
                }
            ),
            "additionalProperties": False,
        },
        handler=_handle_immunizations,
    ),
    "get_appointments": ChatToolSpec(
        name="get_appointments",
        description="Fetch the patient's Appointment records, newest first by appointment date.",
        parameters={
            "type": "object",
            "properties": _dated_search_properties(extra={}),
            "additionalProperties": False,
        },
        handler=_handle_appointments,
    ),
    "get_care_plan_goals": ChatToolSpec(
        name="get_care_plan_goals",
        description=(
            "Fetch the patient's CarePlan and Goal resources. Use for care "
            "plan, goal, target, or planned intervention questions."
        ),
        parameters={
            "type": "object",
            "properties": _dated_search_properties(
                extra={
                    "category": {
                        "type": "string",
                        "description": "Optional CarePlan category filter.",
                    },
                }
            ),
            "additionalProperties": False,
        },
        handler=_handle_care_plan_goals,
    ),
    "list_unindexed_documents": ChatToolSpec(
        name="list_unindexed_documents",
        description=(
            "List documents recently uploaded to the patient chart that have "
            "not yet been indexed/extracted. Returns lightweight manifest rows "
            "(filename, mimetype, docdate, inferred_document_type) so the "
            "extractor can decide which documents are worth extracting."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=_handle_list_unindexed_documents,
    ),
    "extract_documents": ChatToolSpec(
        name="extract_documents",
        description=(
            "Run extraction on one or more uploaded documents and block until "
            "the ingestion job completes. document_type must be one of "
            "'lab_report' or 'intake_form'. Returns no rows directly — "
            "extracted lab values land in FHIR Observation (queryable via "
            "get_lab_trend / get_observations) and intake answers land in "
            "FHIR QuestionnaireResponse (queryable via "
            "get_questionnaire_responses); both surfaces carry an "
            "aiProvenance field for source citation. Pick extract_documents "
            "only after list_unindexed_documents has shown a relevant "
            "document."
        ),
        parameters={
            "type": "object",
            "properties": {
                "documents": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "document_id": {
                                "type": "integer",
                                "description": (
                                    "OpenEMR documents.id (the integer 'id' field "
                                    "from list_unindexed_documents)."
                                ),
                            },
                            "document_type": {
                                "type": "string",
                                "enum": ["lab_report", "intake_form"],
                            },
                        },
                        "required": ["document_id", "document_type"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["documents"],
            "additionalProperties": False,
        },
        handler=_handle_extract_documents,
    ),
}


EXTRACTOR_TOOL_NAMES: frozenset[str] = frozenset(
    {"list_unindexed_documents", "extract_documents"}
)
"""Tools the extractor worker is allowed to call."""

EVIDENCE_TOOL_NAMES: frozenset[str] = frozenset(
    name for name in CHAT_TOOL_REGISTRY if name not in EXTRACTOR_TOOL_NAMES
)
"""Tools the evidence_retriever worker is allowed to call."""


def chat_tools_schema() -> list[dict[str, object]]:
    return [tool.schema() for tool in CHAT_TOOL_REGISTRY.values()]


def extractor_tools_schema() -> list[dict[str, object]]:
    return [
        CHAT_TOOL_REGISTRY[name].schema()
        for name in EXTRACTOR_TOOL_NAMES
        if name in CHAT_TOOL_REGISTRY
    ]


def evidence_tools_schema() -> list[dict[str, object]]:
    return [
        CHAT_TOOL_REGISTRY[name].schema()
        for name in EVIDENCE_TOOL_NAMES
        if name in CHAT_TOOL_REGISTRY
    ]


async def execute_chat_tool(
    call: LlmToolCall,
    client: FhirClient,
    patient_uuid: str,
) -> tuple[list[TypedRow], ToolError | None, dict[str, object]]:
    spec = CHAT_TOOL_REGISTRY.get(call.name)
    if spec is None:
        return (
            [],
            ToolError(tool_name=call.name, message=f"unknown tool {call.name!r}"),
            {"error": f"unknown tool {call.name!r}"},
        )

    try:
        rows = await spec.handler(client, patient_uuid, call.arguments)
    except ValueError as exc:
        return (
            [],
            ToolError(tool_name=call.name, message=str(exc)),
            {"error": str(exc)},
        )
    except FhirError as exc:
        return (
            [],
            ToolError(
                tool_name=call.name,
                message=str(exc),
                status_code=exc.status_code,
            ),
            {"error": f"FHIR error: {exc}"},
        )

    return (
        rows,
        None,
        {
            "rows": [
                {
                    "resource_type": row.resource_type,
                    "resource_id": row.resource_id,
                    "patient_id": row.patient_id,
                    "last_updated": row.last_updated.isoformat(),
                    "fields": row.fields,
                    "verbatim_excerpt": row.verbatim_excerpt,
                }
                for row in rows
            ],
        },
    )
