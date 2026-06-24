"""
Orquestador LangGraph — grafo compilado (S7).

Cambios respecto a S6:
  - Nodo tester agregado después del reviewer.
  - reviewer ahora emite reviewing_passed en lugar de dispatching cuando aprueba.
  - Nuevo edge condicional: tester -> dispatcher | coder | END

Flujo:
  START → architect → dispatcher → coder → opa_gate → reviewer ──┬──> tester ──┬──> dispatcher → END
                          ↑                                       │             │
                          └──────────── skip ◄────────────────────┘             └──> coder (reintento Tester)
                                                                  └──> coder (reintento Reviewer)
"""

from langgraph.graph import END, START, StateGraph

from app.orchestrator.nodes import (
    architect_node,
    coder_node,
    dispatcher_node,
    opa_gate_node,
    reviewer_node,
    tester_node,
    skip_node,
    route_after_coder,
    route_after_dispatch,
    route_after_reviewer,
    route_after_tester,
)
from app.orchestrator.state import GraphState


def build_graph() -> StateGraph:
    builder = StateGraph(GraphState)

    builder.add_node("architect", architect_node)
    builder.add_node("dispatcher", dispatcher_node)
    builder.add_node("coder", coder_node)
    builder.add_node("opa_gate", opa_gate_node)
    builder.add_node("reviewer", reviewer_node)
    builder.add_node("tester", tester_node)
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
            "tester": "tester",
            "coder": "coder",
            END: END,
        },
    )

    builder.add_conditional_edges(
        "tester",
        route_after_tester,
        {
            "dispatcher": "dispatcher",
            "coder": "coder",
            END: END,
        },
    )

    return builder


compiled_graph = build_graph().compile()
