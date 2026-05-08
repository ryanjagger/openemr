"""Prompts for the supervisor + worker chat graph.

Four prompt builders, one per node:

* ``build_supervisor_messages`` / ``supervisor_response_format`` — routing
  decision (``next`` ∈ {extractor, evidence_retriever, finalize}). Pure
  router: no tools, structured JSON only.
* ``build_extractor_messages`` — extractor worker tool-loop prompt. Has the
  list of unindexed documents, the user's last message, and brief context.
* ``build_evidence_messages`` — evidence_retriever worker tool-loop prompt.
  Mirrors ``prompts_chat`` but excludes extractor tools.
* ``build_finalize_messages`` — final ChatTurn synthesis. Reuses the chat
  envelope schema from ``prompts_chat``.

The verifier still owns correctness: prompts can drift, but the verifier is
deterministic and runs on every output.
"""

from __future__ import annotations

from typing import Any

from oe_ai_agent.llm.prompts import _detail_for_row, _note_for_row
from oe_ai_agent.llm.prompts_chat import _chat_system_prompt, chat_response_format
from oe_ai_agent.schemas.chat import ChatFactType, ChatMessage
from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.schemas.unindexed_document import UnindexedDocument

SUPERVISOR_ROUTES = ("extractor", "evidence_retriever", "finalize")


def supervisor_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "SupervisorRoute",
            "schema": {
                "type": "object",
                "properties": {
                    "next": {"type": "string", "enum": list(SUPERVISOR_ROUTES)},
                    "reason": {"type": "string"},
                },
                "required": ["next", "reason"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }


def build_supervisor_messages(
    *,
    patient_uuid: str,
    history: list[ChatMessage],
    cached_context: list[TypedRow],
    unindexed_documents: list[UnindexedDocument],
    supervisor_decisions: list[str],
    extractor_runs: int,
    evidence_runs: int,
) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": _supervisor_system_prompt()},
        {
            "role": "user",
            "content": _supervisor_user_prompt(
                patient_uuid=patient_uuid,
                history=history,
                cached_context=cached_context,
                unindexed_documents=unindexed_documents,
                supervisor_decisions=supervisor_decisions,
                extractor_runs=extractor_runs,
                evidence_runs=evidence_runs,
            ),
        },
    ]


def build_extractor_messages(
    *,
    patient_uuid: str,
    last_user_message: str,
    unindexed_documents: list[UnindexedDocument],
    cached_context: list[TypedRow],
) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": _extractor_system_prompt()},
        {
            "role": "user",
            "content": _extractor_user_prompt(
                patient_uuid=patient_uuid,
                last_user_message=last_user_message,
                unindexed_documents=unindexed_documents,
                cached_context=cached_context,
            ),
        },
    ]


def build_evidence_messages(
    *,
    patient_uuid: str,
    history: list[ChatMessage],
    cached_context: list[TypedRow],
    allowed_types: frozenset[ChatFactType],
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _evidence_system_prompt(allowed_types)},
        {
            "role": "user",
            "content": _chart_context_block(patient_uuid, cached_context),
        },
    ]
    for msg in history:
        messages.append({"role": msg.role.value, "content": msg.content})
    return messages


def build_finalize_messages(
    *,
    patient_uuid: str,
    cached_context: list[TypedRow],
    history: list[ChatMessage],
    allowed_types: frozenset[ChatFactType],
    extraction_pending: bool = False,
) -> list[dict[str, Any]]:
    chart_block = _chart_context_block(patient_uuid, cached_context)
    if extraction_pending:
        # The user uploaded a document just before asking, but ingestion
        # didn't finish in time for this turn. Tell finalize to surface
        # that explicitly so the user knows to retry rather than read
        # "no data" as "the document had nothing relevant."
        chart_block += (
            "\nDOCUMENT EXTRACTION STATUS:\n"
            "- A document the user just uploaded is still being extracted "
            "in the background. Any answers about that document will not "
            "be in the chart context yet. If the user is asking about a "
            "recently uploaded document, explicitly tell them: "
            "'document extraction is still running — please ask again in "
            "about 30 seconds.' Do not claim the document had no relevant "
            "data when the extraction has not finished.\n"
        )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _chat_system_prompt(allowed_types)},
        {"role": "user", "content": chart_block},
    ]
    for msg in history:
        messages.append({"role": msg.role.value, "content": msg.content})
    return messages


def finalize_response_format(allowed_types: frozenset[ChatFactType]) -> dict[str, Any]:
    return chat_response_format(allowed_types)


def _supervisor_system_prompt() -> str:
    return (
        "You are the SUPERVISOR of an OpenEMR co-pilot chat. You decide what "
        "happens next in this turn. You do NOT answer the user.\n"
        "\n"
        "Choose exactly one route:\n"
        "  - 'extractor': hand off to the extractor worker. Pick this only "
        "    when the user's question is plausibly answered by an uploaded "
        "    but not-yet-indexed document, AND there is at least one "
        "    UNINDEXED DOCUMENT in the list, AND the extractor has not "
        "    already run for this question. Examples: user asks about "
        "    recent labs and an unindexed lab report exists; user asks about "
        "    intake answers and an unindexed intake form exists.\n"
        "  - 'evidence_retriever': hand off to the evidence worker to fetch "
        "    chart data, indexed documents, or clinical guidelines. This is "
        "    the default for chart questions. Use it for medications, "
        "    problems, allergies, encounters, immunizations, observations, "
        "    indexed-document facts, guideline lookups, etc.\n"
        "  - 'finalize': enough context has been gathered. Hand to the "
        "    finalize node which writes the answer and citations. Pick this "
        "    when the cached context already contains the rows that will "
        "    answer the question, OR when the workers have already run and "
        "    further calls are unlikely to help.\n"
        "\n"
        "Rules:\n"
        "- Do not pick 'extractor' when the unindexed-documents list is "
        "  empty. Pick 'evidence_retriever' or 'finalize' instead.\n"
        "- Do not loop: if the same worker has run twice in this turn, "
        "  prefer 'finalize'.\n"
        "- 'reason' is a short sentence (<=140 chars). Cite which fact or "
        "  document drove the decision.\n"
        "\n"
        "Respond ONLY with JSON conforming to the SupervisorRoute schema."
    )


def _supervisor_user_prompt(
    *,
    patient_uuid: str,
    history: list[ChatMessage],
    cached_context: list[TypedRow],
    unindexed_documents: list[UnindexedDocument],
    supervisor_decisions: list[str],
    extractor_runs: int,
    evidence_runs: int,
) -> str:
    last_user = _last_user_message(history) or "(no user message yet)"
    return (
        f"PATIENT: {patient_uuid}\n"
        f"\nLAST USER MESSAGE:\n{last_user}\n"
        f"\nUNINDEXED DOCUMENTS ({len(unindexed_documents)}):\n"
        f"{_format_unindexed_documents(unindexed_documents)}\n"
        f"\nCACHED CONTEXT SUMMARY:\n"
        f"{_format_context_summary(cached_context)}\n"
        f"\nWORKERS ALREADY RUN THIS TURN:\n"
        f"  extractor_runs={extractor_runs}, evidence_runs={evidence_runs}\n"
        f"  decisions_so_far={supervisor_decisions or '[]'}\n"
    )


def _extractor_system_prompt() -> str:
    return (
        "You are the EXTRACTOR worker. Your job: extract the unindexed "
        "uploaded document(s) most relevant to the user's question, then "
        "stop. You do NOT answer the user — the finalize step does that.\n"
        "\n"
        "Tools:\n"
        "- list_unindexed_documents: re-pull the unindexed list (rarely "
        "  needed; the list below is fresh).\n"
        "- extract_documents: takes an array of {document_id, document_type} "
        "  selections. Block-extracts them; the extracted clinical data "
        "  lands in FHIR Observation (lab) or QuestionnaireResponse (intake) "
        "  and is fetched later by evidence_retriever. Each call may "
        "  extract multiple documents.\n"
        "\n"
        "Rules:\n"
        "- Pick documents whose filename, category, or inferred_document_type "
        "  matches the user's question. Do NOT extract everything; be "
        "  conservative — extraction is expensive.\n"
        "- For 'document_type' use the inferred_document_type from the list "
        "  when present; otherwise infer from filename ('lab_report' or "
        "  'intake_form').\n"
        "- After extract_documents returns, reply with a brief plain-text "
        "  status (one short sentence). Do not write JSON, do not narrate "
        "  the user, do not advise.\n"
        "- If extract_documents reports an EXTRACTION_PENDING error, the "
        "  ingestion is still running in the background. Reply with a "
        "  single sentence saying extraction is still in progress; do NOT "
        "  retry the extraction this turn. The finalize step will tell "
        "  the user to ask again shortly.\n"
        "- If no document is clearly relevant, respond with a single "
        "  sentence saying so and call no tools.\n"
    )


def _extractor_user_prompt(
    *,
    patient_uuid: str,
    last_user_message: str,
    unindexed_documents: list[UnindexedDocument],
    cached_context: list[TypedRow],
) -> str:
    return (
        f"PATIENT: {patient_uuid}\n"
        f"\nUSER QUESTION:\n{last_user_message}\n"
        f"\nUNINDEXED DOCUMENTS:\n"
        f"{_format_unindexed_documents(unindexed_documents)}\n"
        f"\nCURRENT CONTEXT SUMMARY:\n"
        f"{_format_context_summary(cached_context)}\n"
    )


def _evidence_system_prompt(allowed_types: frozenset[ChatFactType]) -> str:
    base = _chat_system_prompt(allowed_types)
    return (
        base + "\n\nROLE-SPECIFIC NOTE:\n"
        "- You are running as the EVIDENCE_RETRIEVER worker inside a "
        "  supervisor graph. Focus on calling the right chart/guideline/"
        "  indexed-document tools to add evidence rows. The finalize node "
        "  will synthesize the user-facing envelope from the rows you "
        "  collect, but you SHOULD still emit a valid ChatTurn envelope "
        "  when you have enough context — finalize will refine it.\n"
    )


def _chart_context_block(patient_uuid: str, tool_rows: list[TypedRow]) -> str:
    if not tool_rows:
        return f"PATIENT: {patient_uuid}\nCONTEXT: (no rows)\n"
    context_lines = []
    for row in tool_rows:
        note = _note_for_row(row)
        suffix = f" [{note}]" if note else ""
        context_lines.append(f"- {row.resource_type}/{row.resource_id}{suffix}")
    return (
        f"PATIENT: {patient_uuid}\n\n"
        "CONTEXT (cite by exact ResourceType/id):\n"
        + "\n".join(context_lines)
        + "\n\nROW DETAILS:\n"
        + "\n".join(_detail_for_row(row) for row in tool_rows)
        + "\n"
    )


def _format_unindexed_documents(documents: list[UnindexedDocument]) -> str:
    if not documents:
        return "  (none)"
    lines = []
    for doc in documents:
        type_hint = doc.inferred_document_type or "?"
        category = f", category={doc.category_name}" if doc.category_name else ""
        date_part = f", docdate={doc.docdate}" if doc.docdate else ""
        lines.append(
            f"  - id={doc.document_id} uuid={doc.document_uuid} "
            f"type_hint={type_hint} filename={doc.filename}"
            f"{date_part}{category}"
        )
    return "\n".join(lines)


def _format_context_summary(rows: list[TypedRow]) -> str:
    if not rows:
        return "  (empty)"
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.resource_type] = counts.get(row.resource_type, 0) + 1
    return "\n".join(
        f"  - {rtype}: {count}" for rtype, count in sorted(counts.items(), key=lambda kv: kv[0])
    )


def _last_user_message(history: list[ChatMessage]) -> str | None:
    for msg in reversed(history):
        if msg.role.value == "user":
            return msg.content
    return None
