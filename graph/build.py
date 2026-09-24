from langgraph.graph import StateGraph, END

from graph.state import DischargeState
from graph.nodes import (
    extract_care_plan,
    classify_intent,
    answer_question,
    triage_symptom,
    escalate,
    log_normal,
)


def route_by_intent(state: DischargeState) -> str:
    """Conditional edge after classify_intent."""
    return "answer_question" if state["intent"] == "question" else "triage_symptom"


def route_by_risk(state: DischargeState) -> str:
    """Conditional edge after triage_symptom."""
    return "escalate" if state["risk_level"] == "high" else "log_normal"


def route_by_groundedness(state: DischargeState) -> str:
    """Conditional edge after answer_question -- an ungrounded answer escalates too."""
    return "end" if state.get("grounded", True) else "escalate"


def build_graph():
    graph = StateGraph(DischargeState)

    graph.add_node("extract_care_plan", extract_care_plan)
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("answer_question", answer_question)
    graph.add_node("triage_symptom", triage_symptom)
    graph.add_node("escalate", escalate)
    graph.add_node("log_normal", log_normal)

    graph.set_entry_point("extract_care_plan")
    graph.add_edge("extract_care_plan", "classify_intent")

    graph.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {
            "answer_question": "answer_question",
            "triage_symptom": "triage_symptom",
        },
    )

    graph.add_conditional_edges(
        "triage_symptom",
        route_by_risk,
        {
            "escalate": "escalate",
            "log_normal": "log_normal",
        },
    )

    graph.add_conditional_edges(
        "answer_question",
        route_by_groundedness,
        {
            "end": END,
            "escalate": "escalate",
        },
    )

    graph.add_edge("escalate", END)
    graph.add_edge("log_normal", END)

    return graph.compile()