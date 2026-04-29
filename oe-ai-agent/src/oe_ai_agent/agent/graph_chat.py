"""LangGraph builder for the chat agent.

Linear chain: ``START → ensure_context → llm_turn → parse_envelope →
verify_chat → END``. The tool-calling loop lives inside ``llm_turn``;
LangGraph stays linear so the state shape is easy to reason about.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.agent.nodes.ensure_context import ensure_context_node
from oe_ai_agent.agent.nodes.llm_turn import make_llm_turn_node
from oe_ai_agent.agent.nodes.parse_envelope import parse_envelope_node
from oe_ai_agent.agent.nodes.verify_chat import make_verify_chat_node
from oe_ai_agent.llm.client import LlmClient
from oe_ai_agent.schemas.brief import BriefItemType


def build_chat_graph(
    llm: LlmClient,
    *,
    allowed_types: frozenset[BriefItemType] | None = None,
) -> object:
    types = allowed_types if allowed_types is not None else frozenset(BriefItemType)
    builder = StateGraph(ChatState)
    builder.add_node("ensure_context", ensure_context_node)
    builder.add_node("llm_turn", make_llm_turn_node(llm, allowed_types=types))  # type: ignore[arg-type]
    builder.add_node("parse_envelope", parse_envelope_node)
    builder.add_node("verify_chat", make_verify_chat_node(types))  # type: ignore[arg-type]

    builder.add_edge(START, "ensure_context")
    builder.add_edge("ensure_context", "llm_turn")
    builder.add_edge("llm_turn", "parse_envelope")
    builder.add_edge("parse_envelope", "verify_chat")
    builder.add_edge("verify_chat", END)

    return builder.compile()
