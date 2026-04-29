"""llm_call node — composes prompt, calls the LLM, captures the raw response."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from oe_ai_agent.agent.state import AgentState
from oe_ai_agent.llm.client import LlmClient
from oe_ai_agent.llm.prompts import build_messages, response_format

LlmCallNode = Callable[[AgentState], Awaitable[dict[str, object]]]


def make_llm_call_node(llm: LlmClient) -> LlmCallNode:
    async def llm_call_node(state: AgentState) -> dict[str, object]:
        messages = build_messages(state.patient_uuid, state.tool_results)
        raw = await llm.chat(messages, response_format=response_format())
        return {"raw_llm_output": raw}

    return llm_call_node
