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
    """
    Conditional edge after classify_intent.
    LangGraph calls this with the current state and expects back the NAME
    of the next node to run.
    """
    return "answer_question" if state["intent"] == "question" else "triage_symptom"


def route_by_risk(state: DischargeState) -> str:
    """Conditional edge after triage_symptom."""
    return "escalate" if state["risk_level"] == "high" else "log_normal"


def route_by_groundedness(state: DischargeState) -> str:
    """Conditional edge after answer_question -- an ungrounded answer escalates too."""
    return "end" if state.get("grounded", True) else "escalate"


def build_graph():
    graph = StateGraph(DischargeState)

    # Register every node
    graph.add_node("extract_care_plan", extract_care_plan)
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("answer_question", answer_question)
    graph.add_node("triage_symptom", triage_symptom)
    graph.add_node("escalate", escalate)
    graph.add_node("log_normal", log_normal)

    # Entry point: every run starts by extracting the care plan
    graph.set_entry_point("extract_care_plan")
    graph.add_edge("extract_care_plan", "classify_intent")

    # Branch 1: question vs symptom report (diagram 2's first split)
    graph.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {
            "answer_question": "answer_question",
            "triage_symptom": "triage_symptom",
        },
    )

    # Branch 2: escalate vs log normal (diagram 2's second split)
    graph.add_conditional_edges(
        "triage_symptom",
        route_by_risk,
        {
            "escalate": "escalate",
            "log_normal": "log_normal",
        },
    )

    # Branch 3: grounded answers end the run; ungrounded ones escalate.
    # This is the "escalate when unsure" requirement from the client brief.
    graph.add_conditional_edges(
        "answer_question",
        route_by_groundedness,
        {
            "end": END,
            "escalate": "escalate",
        },
    )

    # The two endpoint nodes still end normally.
    graph.add_edge("escalate", END)
    graph.add_edge("log_normal", END)

    return graph.compile()