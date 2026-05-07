"""ensure_chat_context node — populate unindexed-doc manifests; no chart prefetch.

Chat is user-directed, so we don't eagerly prefetch the chart. But we DO
cheaply enumerate unindexed uploaded documents up front: the supervisor
needs to see them to decide whether to hand off to the extractor worker.
This is one HTTP GET to OpenEMR — no LLM, no FHIR.

If the lookup fails (PHP module not installed, network error), the node
swallows the failure and proceeds with an empty list. The supervisor will
treat that as 'no extraction needed.'
"""

from __future__ import annotations

import httpx

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.observability import get_logger, step
from oe_ai_agent.tools.fhir_client import FhirClient, FhirError
from oe_ai_agent.tools.unindexed_documents import list_unindexed_documents

logger = get_logger(__name__)


async def ensure_chat_context_node(state: ChatState) -> dict[str, object]:
    async with step("ensure_chat_context") as record:
        unindexed = state.unindexed_documents
        if not unindexed:
            try:
                async with FhirClient(
                    base_url=state.fhir_base_url,
                    bearer_token=state.bearer_token.get_secret_value(),
                    request_id=state.request_id,
                ) as client:
                    unindexed = await list_unindexed_documents(client, state.patient_uuid)
            except (FhirError, httpx.HTTPError) as exc:
                logger.warning(
                    "ensure_chat_context: unindexed lookup failed; supervisor "
                    "will see empty list",
                    status_code=getattr(exc, "status_code", None),
                    detail=str(exc)[:200],
                )
                unindexed = []

        record.attrs.update(
            {
                "cache_hit": bool(state.cached_context),
                "row_count": len(state.cached_context),
                "eager_prefetch": False,
                "unindexed_count": len(unindexed),
            }
        )
    return {"unindexed_documents": unindexed}
