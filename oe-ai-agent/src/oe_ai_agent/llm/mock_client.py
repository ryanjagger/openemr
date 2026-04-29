"""Deterministic mock LLM for Phase 3a + tests.

Two modes:

* ``MockLlmClient(scripted=<str|callable>)`` — fixed string or
  callable-of-(messages, response_format)→string. Used by unit tests where
  the prompt's exact content is known.
* ``MockLlmClient.synthesizing()`` — sniffs the prompt for a ``CONTEXT``
  section listing tool rows (resource_type, resource_id), then emits a
  small, schema-valid ``BriefResponse`` JSON that cites real rows. Used by
  the integration path so the verifier sees consistent input.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

_CONTEXT_LINE_RE = re.compile(
    r"^- (?P<rtype>\w+)/(?P<rid>[\w\-:]+)(?: \[(?P<note>[^\]]*)\])?$",
    re.MULTILINE,
)

_MAX_SYNTHESIZED_ITEMS = 5


class MockLlmClient:
    model_id = "mock"

    def __init__(
        self,
        scripted: str | Callable[[list[dict[str, str]], dict[str, Any] | None], str],
    ) -> None:
        self._scripted = scripted

    async def chat(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None = None,
    ) -> str:
        if callable(self._scripted):
            return self._scripted(messages, response_format)
        return self._scripted

    @classmethod
    def synthesizing(cls) -> MockLlmClient:
        return cls(scripted=_synthesize_from_context)


def _synthesize_from_context(
    messages: list[dict[str, str]],
    _response_format: dict[str, Any] | None,
) -> str:
    """Build a BriefResponse JSON that cites real rows surfaced in the prompt."""
    blob = "\n".join(m.get("content", "") for m in messages)
    rows = list(_CONTEXT_LINE_RE.finditer(blob))
    items: list[dict[str, Any]] = []

    for match in rows:
        rtype = match["rtype"]
        rid = match["rid"]
        note = match["note"] or rtype
        item = _maybe_item_for_row(rtype, rid, note)
        if item is not None:
            items.append(item)
        if len(items) >= _MAX_SYNTHESIZED_ITEMS:
            break

    return json.dumps({"items": items, "verification_failures": []})


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
