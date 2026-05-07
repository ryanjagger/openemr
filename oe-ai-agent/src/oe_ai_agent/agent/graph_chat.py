"""LangGraph builder for the chat agent — supervisor + workers.

Topology::

    START
      → ensure_chat_context
      → supervisor ⇄ {extractor, evidence_retriever}
                  ↓ (when supervisor → finalize)
      → finalize
      → parse_envelope
      → verify_chat
      → END

The supervisor decides per turn whether to hand off to the extractor
worker (extract unindexed uploaded documents), the evidence_retriever
worker (call FHIR/indexed-document/guideline tools), or finalize. Workers
return ``Command(goto="supervisor")`` after running. Only ``finalize``
writes the envelope; ``parse_envelope`` + ``verify_chat`` then run
unchanged from the legacy linear graph.

Iteration safety: ``ChatState.supervisor_turns_remaining`` (default 6) is
the global cap; workers also have their own per-run iteration caps; and
the supervisor strips already-exhausted routes from the LLM's choices.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from oe_ai_agent.agent.chat_state import ChatState
from oe_ai_agent.agent.nodes.ensure_chat_context import ensure_chat_context_node
from oe_ai_agent.agent.nodes.evidence_retriever import make_evidence_retriever_node
from oe_ai_agent.agent.nodes.extractor import make_extractor_node
from oe_ai_agent.agent.nodes.finalize import make_finalize_node
from oe_ai_agent.agent.nodes.parse_envelope import parse_envelope_node
from oe_ai_agent.agent.nodes.supervisor import make_supervisor_node
from oe_ai_agent.agent.nodes.verify_chat import make_verify_chat_node
from oe_ai_agent.llm.client import LlmClient
from oe_ai_agent.schemas.chat import ChatFactType


def build_chat_graph(
    llm: LlmClient,
    *,
    allowed_types: frozenset[ChatFactType] | None = None,
) -> object:
    types = allowed_types if allowed_types is not None else frozenset(ChatFactType)
    builder = StateGraph(ChatState)
    builder.add_node("ensure_chat_context", ensure_chat_context_node)
    builder.add_node(
        "supervisor",
        make_supervisor_node(llm),  # type: ignore[arg-type]
        destinations=("extractor", "evidence_retriever", "finalize"),
    )
    builder.add_node(
        "extractor",
        make_extractor_node(llm),  # type: ignore[arg-type]
        destinations=("supervisor",),
    )
    builder.add_node(
        "evidence_retriever",
        make_evidence_retriever_node(llm, allowed_types=types),  # type: ignore[arg-type]
        destinations=("supervisor",),
    )
    builder.add_node("finalize", make_finalize_node(llm, allowed_types=types))  # type: ignore[arg-type]
    builder.add_node("parse_envelope", parse_envelope_node)
    builder.add_node("verify_chat", make_verify_chat_node(types))  # type: ignore[arg-type]

    builder.add_edge(START, "ensure_chat_context")
    builder.add_edge("ensure_chat_context", "supervisor")
    builder.add_edge("finalize", "parse_envelope")
    builder.add_edge("parse_envelope", "verify_chat")
    builder.add_edge("verify_chat", END)

    return builder.compile()
