"""Deterministic mock LLM for Phase 3a + tests.

Modes:

* ``MockLlmClient(scripted=<str|callable>)`` — fixed string or
  callable-of-(messages, response_format)→string. Used by unit tests where
  the prompt's exact content is known.
* ``MockLlmClient.synthesizing()`` — sniffs the prompt for a ``CONTEXT``
  section listing tool rows (resource_type, resource_id), then emits a
  small, schema-valid ``BriefResponse`` JSON that cites real rows. Used by
  the integration path so the verifier sees consistent input.
* ``MockLlmClient(chat_scripted=...)`` — scripted ``chat_with_tools``
  responder for chat-graph tests. Accepts a fixed ``LlmChatResult`` or a
  callable that receives the messages and returns one.

Mock responses default to a zero-valued ``LlmUsage``; tests that care about
observability can pass ``default_usage=...`` to override it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from oe_ai_agent.llm.client import LlmChatResult, LlmCompletionResult, LlmUsage

_CONTEXT_LINE_RE = re.compile(
    r"^- (?P<rtype>\w+)/(?P<rid>[\w\-:]+)(?: \[(?P<note>[^\]]*)\])?$",
    re.MULTILINE,
)

_MAX_SYNTHESIZED_ITEMS = 5

ChatScript = LlmChatResult | Callable[[list[dict[str, Any]]], LlmChatResult]


class MockLlmClient:
    model_id = "mock"

    def __init__(
        self,
        scripted: str | Callable[[list[dict[str, Any]], dict[str, Any] | None], str] | None = None,
        *,
        chat_scripted: ChatScript | None = None,
        default_usage: LlmUsage | None = None,
    ) -> None:
        self._scripted = scripted
        self._chat_scripted = chat_scripted
        self._default_usage = default_usage or LlmUsage()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
        *,
        max_tokens: int | None = None,
    ) -> LlmCompletionResult:
        del max_tokens
        if self._scripted is None:
            raise RuntimeError(
                "MockLlmClient.chat() called without a scripted responder",
            )
        if callable(self._scripted):
            text = self._scripted(messages, response_format)
        else:
            text = self._scripted
        return LlmCompletionResult(content=text, usage=self._default_usage)

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LlmChatResult:
        if self._chat_scripted is None:
            # Fall back to .chat()-style scripting if only a string was provided —
            # tests that don't need tool_calls can still drive chat_with_tools.
            completion = await self.chat(messages, response_format)
            return LlmChatResult(
                content=completion.content,
                tool_calls=[],
                usage=completion.usage,
            )
        if callable(self._chat_scripted):
            result = self._chat_scripted(messages)
        else:
            result = self._chat_scripted
        # If the script didn't supply a usage, attach the default so the
        # downstream pipeline always has a non-None usage to read.
        if result.usage == LlmUsage():
            result = LlmChatResult(
                content=result.content,
                tool_calls=list(result.tool_calls),
                usage=self._default_usage,
            )
        return result

    @classmethod
    def synthesizing(cls) -> MockLlmClient:
        return cls(scripted=_synthesize_from_context)


def _synthesize_from_context(
    messages: list[dict[str, Any]],
    response_format: dict[str, Any] | None,
) -> str:
    """Build schema-valid JSON that cites real rows surfaced in the prompt."""
    blob = "\n".join(_content_to_text(m.get("content", "")) for m in messages)
    schema_name = (
        response_format.get("json_schema", {}).get("name")
        if response_format is not None
        else None
    )
    if schema_name == "SupervisorRoute":
        return _synthesize_supervisor_route(blob)
    rows = list(_CONTEXT_LINE_RE.finditer(blob))
    items: list[dict[str, Any]] = []
    is_chat = schema_name == "ChatTurn"

    for match in rows:
        rtype = match["rtype"]
        rid = match["rid"]
        note = match["note"] or rtype
        item = (
            _maybe_chat_fact_for_row(rtype, rid, note)
            if is_chat
            else _maybe_item_for_row(rtype, rid, note)
        )
        if item is not None:
            items.append(item)
        if len(items) >= _MAX_SYNTHESIZED_ITEMS:
            break

    if is_chat:
        if not items:
            return json.dumps({"narrative": "I do not see that in the chart context.", "facts": []})
        return json.dumps(
            {
                "narrative": "I found chart facts in the verified cards below.",
                "facts": items,
            }
        )

    return json.dumps({"items": items, "verification_failures": []})


def _synthesize_supervisor_route(prompt_blob: str) -> str:
    """Pick a deterministic supervisor route from prompt content.

    Heuristics — these only run in mock mode where there is no real
    supervisor LLM. They mimic the rules in ``prompts_supervisor`` so
    integration tests with the synthesizing mock take a sensible path:

    * If the unindexed-documents block is non-empty AND the extractor has
      not yet run AND the user message looks like a document question,
      route to ``extractor``.
    * Else if evidence_retriever has not yet run, route to
      ``evidence_retriever`` so the model gathers chart context.
    * Else route to ``finalize``.
    """
    has_unindexed = "UNINDEXED DOCUMENTS (0)" not in prompt_blob and (
        "UNINDEXED DOCUMENTS" in prompt_blob
    )
    extractor_runs_zero = "extractor_runs=0" in prompt_blob
    evidence_runs_zero = "evidence_runs=0" in prompt_blob
    user_msg_lower = prompt_blob.lower()
    document_intent = any(
        token in user_msg_lower
        for token in ("upload", "lab report", "intake", "document", "pdf", "scan")
    )

    if has_unindexed and extractor_runs_zero and document_intent:
        return json.dumps({"next": "extractor", "reason": "mock: unindexed doc relevant"})
    if evidence_runs_zero:
        return json.dumps(
            {"next": "evidence_retriever", "reason": "mock: gather chart evidence"}
        )
    return json.dumps({"next": "finalize", "reason": "mock: workers finished"})


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "\n".join(parts)
    return ""


def _maybe_item_for_row(rtype: str, rid: str, note: str) -> dict[str, Any] | None:
    """One row of context → at most one BriefItem of an appropriate type.

    Intentionally narrow: only synthesize types where the row→type mapping is
    unambiguous. The verifier will catch any drift here.
    """
    citation = [{"resource_type": rtype, "resource_id": rid}]
    if rtype == "MedicationRequest":
        return {
            "type": "med_current",
            "text": f"On {note}",
            "verbatim_excerpts": [note],
            "citations": citation,
        }
    if rtype == "AllergyIntolerance":
        return {
            "type": "allergy",
            "text": f"Allergy: {note}",
            "verbatim_excerpts": [note],
            "citations": citation,
        }
    if rtype == "Encounter":
        return {
            "type": "recent_event",
            "text": f"Recent visit: {note}",
            "verbatim_excerpts": [note],
            "citations": citation,
        }
    return None


def _maybe_chat_fact_for_row(rtype: str, rid: str, note: str) -> dict[str, Any] | None:
    citation = [{"resource_type": rtype, "resource_id": rid}]
    mapping = {
        "Patient": ("demographics", "Demographics"),
        "MedicationRequest": ("medication", "Medication"),
        "AllergyIntolerance": ("allergy", "Allergy"),
        "Observation": ("observation", "Observation"),
        "Encounter": ("encounter", "Encounter"),
        "DocumentReference": ("note", "Note"),
        "IndexedDocumentFact": ("document_fact", "Indexed document fact"),
        "ClinicalGuidelineChunk": ("guideline", "Guideline"),
        "Appointment": ("appointment", "Appointment"),
    }
    if rtype in {"CarePlan", "Goal"}:
        fact_type, label = "care_plan", "Care plan"
    else:
        mapped = mapping.get(rtype)
        if mapped is None:
            return None
        fact_type, label = mapped
    return {
        "type": fact_type,
        "text": f"{label}: {note}",
        "verbatim_excerpts": [note],
        "citations": citation,
    }
