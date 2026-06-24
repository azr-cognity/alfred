"""
Orquestador LangGraph — grafo compilado (S8).

Cambios respecto a S7:
  - Nodo auditor agregado como nodo terminal del run.
  - dispatcher emite auditing cuando todas las tasks completan.
  - route_after_dispatch incluye branch "auditor".

Flujo completo:
  START → architect → dispatcher ──────────────────────────────> auditor → END
                          ↑                                          ↑
                          │   coder → opa_gate → reviewer → tester ─┘
                          └── skip ◄──────────────────────────────────
"""

from langgraph.graph import END, START, StateGraph

from app.orchestrator.nodes import (
    architect_node,
    auditor_node,
    coder_node,
    dispatcher_node,
    opa_gate_node,
    reviewer_node,
    tester_node,
    skip_node,
    route_after_auditor,
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
    builder.add_node("auditor", auditor_node)
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
            "auditor": "auditor",
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

    builder.add_conditional_edges(
        "auditor",
        route_after_auditor,
        {END: END},
    )

    return builder


compiled_graph = build_graph().compile()