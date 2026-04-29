"""LangGraph builder for the brief agent.

Linear chain for MVP: ``START → fetch_context → llm_call → parse_output →
verify → END``. Phase 5+ inserts ``paraphrase_check`` (Tier 3) and write-side
``human_approval`` nodes; the graph shape exists so those land additively.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from oe_ai_agent.agent.nodes.fetch_context_node import fetch_context_node
from oe_ai_agent.agent.nodes.llm_call import make_llm_call_node
from oe_ai_agent.agent.nodes.parse_output import parse_output_node
from oe_ai_agent.agent.nodes.verify import make_verify_node
from oe_ai_agent.agent.state import AgentState
from oe_ai_agent.llm.client import LlmClient
from oe_ai_agent.schemas.brief import BriefItemType


def build_graph(
    llm: LlmClient,
    *,
    allowed_types: frozenset[BriefItemType] | None = None,
) -> object:
    types = allowed_types if allowed_types is not None else frozenset(BriefItemType)
    builder = StateGraph(AgentState)
    # LangGraph's _Node generic doesn't infer cleanly across our async
    # closures; runtime behavior is exercised by tests/test_agent_graph.py.
    builder.add_node("fetch_context", fetch_context_node)
    builder.add_node("llm_call", make_llm_call_node(llm, types))  # type: ignore[arg-type]
    builder.add_node("parse_output", parse_output_node)
    builder.add_node("verify", make_verify_node(types))  # type: ignore[arg-type]

    builder.add_edge(START, "fetch_context")
    builder.add_edge("fetch_context", "llm_call")
    builder.add_edge("llm_call", "parse_output")
    builder.add_edge("parse_output", "verify")
    builder.add_edge("verify", END)

    return builder.compile()
