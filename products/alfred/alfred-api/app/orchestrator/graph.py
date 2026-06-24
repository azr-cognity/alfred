"""
Orquestador LangGraph — grafo compilado (S7).

Cambios respecto a S6:
  - reviewer edge condicional ahora incluye branch "coder" para reintentos.

Flujo:
  START → architect → dispatcher → coder → opa_gate → reviewer ─┬─> dispatcher → ... → END
                         ^                                        │
                         └──────── skip ◄── (agentes no impl.)   └─> coder (reintento)
"""

from langgraph.graph import END, START, StateGraph

from app.orchestrator.nodes import (
    architect_node,
    coder_node,
    dispatcher_node,
    opa_gate_node,
    reviewer_node,
    skip_node,
    route_after_coder,
    route_after_dispatch,
    route_after_reviewer,
)
from app.orchestrator.state import GraphState


def build_graph() -> StateGraph:
    builder = StateGraph(GraphState)

    builder.add_node("architect", architect_node)
    builder.add_node("dispatcher", dispatcher_node)
    builder.add_node("coder", coder_node)
    builder.add_node("opa_gate", opa_gate_node)
    builder.add_node("reviewer", reviewer_node)
    builder.add_node("skip", skip_node)

    builder.add_edge(START, "architect")
    builder.add_edge("architect", "dispatcher")
    builder.add_edge("coder", "opa_gate")
    builder.add_edge("skip", "dispatcher")

    builder.add_conditional_edges(
        "dispatcher",
        route_after_dispatch,
        {
            "coder": "coder",
            "skip": "skip",
            END: END,
        },
    )

    builder.add_conditional_edges(
        "opa_gate",
        route_after_coder,
        {
            "reviewer": "reviewer",
            END: END,
        },
    )

    builder.add_conditional_edges(
        "reviewer",
        route_after_reviewer,
        {
            "dispatcher": "dispatcher",
            "coder": "coder",   # nuevo en S7: reintento tras rechazo
            END: END,
        },
    )

    return builder


compiled_graph = build_graph().compile()
