"""finalize node — synthesizes the ChatTurn envelope from gathered context.

Single LLM call, no tools, ChatTurn response_format. The workers can also
emit envelopes opportunistically, but only this node's output is fed to
``parse_envelope`` + ``verify_chat`` — keeping the verifier's
single-envelope assumption intact.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.llm.client import LlmClient
from oe_ai_agent.llm.prompts_supervisor import (
    build_finalize_messages,
    finalize_response_format,
)
from oe_ai_agent.observability import current_trace, get_logger, step
from oe_ai_agent.schemas.chat import ChatFactType

logger = get_logger(__name__)

FinalizeNode = Callable[[ChatState], Awaitable[dict[str, object]]]


def make_finalize_node(
    llm: LlmClient,
    *,
    allowed_types: frozenset[ChatFactType] | None = None,
) -> FinalizeNode:
    types = allowed_types if allowed_types is not None else frozenset(ChatFactType)

    async def finalize_node(state: ChatState) -> dict[str, object]:
        async with step("finalize", model=llm.model_id) as record:
            messages = build_finalize_messages(
                patient_uuid=state.patient_uuid,
                cached_context=state.cached_context,
                history=state.history,
                allowed_types=types,
                extraction_pending=state.extraction_pending,
            )
            result = await llm.chat_with_tools(
                messages,
                tools=None,
                response_format=finalize_response_format(types),
            )
            collector = current_trace()
            if collector is not None:
                collector.add_usage(result.usage)

            record.attrs.update(
                {
                    "prompt_tokens": result.usage.prompt_tokens,
                    "completion_tokens": result.usage.completion_tokens,
                    "latency_ms": result.usage.latency_ms,
                    "context_row_count": len(state.cached_context),
                    "had_envelope": result.content is not None,
                }
            )
            return {"raw_envelope": result.content}

    return finalize_node
