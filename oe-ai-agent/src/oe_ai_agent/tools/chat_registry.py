"""Registry for model-callable chat tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date

from oe_ai_agent.llm.client import LlmToolCall
from oe_ai_agent.schemas.tool_results import ToolError, TypedRow
from oe_ai_agent.tools.fhir_client import FhirClient, FhirError
from oe_ai_agent.tools.immunizations import get_immunizations
from oe_ai_agent.tools.lab_trend import get_lab_trend
from oe_ai_agent.tools.medication_history import get_medication_history
from oe_ai_agent.tools.observation_search import get_observations
from oe_ai_agent.tools.orders import get_orders
from oe_ai_agent.tools.procedures import get_procedures

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
    "get_lab_trend": ChatToolSpec(
        name="get_lab_trend",
        description=(
            "Fetch the patient's historical Observation values for a single "
            "lab, identified by LOINC code (e.g. '4548-4') or free text "
            "(e.g. 'hemoglobin a1c'). Use only when the cached chart context "
            "does not already carry enough history to answer the user's question."
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
            "for broad filters such as 'laboratory' or 'vital-signs'."
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
}


def chat_tools_schema() -> list[dict[str, object]]:
    return [tool.schema() for tool in CHAT_TOOL_REGISTRY.values()]


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
                    "last_updated": row.last_updated.isoformat(),
                    "fields": row.fields,
                }
                for row in rows
            ],
        },
    )
