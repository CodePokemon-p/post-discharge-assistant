from langgraph.graph import StateGraph, END

from graph.state import DischargeState
from graph.nodes import (
    extract_care_plan,
    classify_intent,
    answer_question,
    decline_off_topic,
    triage_symptom,
    escalate,
    log_normal,
)


def route_by_intent(state: DischargeState) -> str:
    """Conditional edge after classify_intent -- three-way split now."""
    intent = state.get("intent")
    if intent == "symptom_report":
        return "triage_symptom"
    if intent == "off_topic":
        return "decline_off_topic"
    return "answer_question"


def route_by_risk(state: DischargeState) -> str:
    """Conditional edge after triage_symptom -- only high risk escalates."""
    return "escalate" if state.get("risk_level") == "high" else "log_normal"


def build_graph():
    graph = StateGraph(DischargeState)

    graph.add_node("extract_care_plan", extract_care_plan)
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("answer_question", answer_question)
    graph.add_node("decline_off_topic", decline_off_topic)
    graph.add_node("triage_symptom", triage_symptom)
    graph.add_node("escalate", escalate)
    graph.add_node("log_normal", log_normal)

    graph.set_entry_point("extract_care_plan")
    graph.add_edge("extract_care_plan", "classify_intent")

    # Branch 1: three-way intent split (question | symptom | off_topic)
    graph.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {
            "answer_question": "answer_question",
            "triage_symptom": "triage_symptom",
            "decline_off_topic": "decline_off_topic",
        },
    )

    # Branch 2: only high-risk symptoms reach the nurse dashboard
    graph.add_conditional_edges(
        "triage_symptom",
        route_by_risk,
        {
            "escalate": "escalate",
            "log_normal": "log_normal",
        },
    )

    # Answer and off-topic paths always end WITHOUT a nurse alert.
    # (Answer node handles ungrounded questions with a soft reply +
    #  log; the low-priority escalation happens inside storage, not
    #  as a real-time alert.)
    graph.add_edge("answer_question", END)
    graph.add_edge("decline_off_topic", END)
    graph.add_edge("escalate", END)
    graph.add_edge("log_normal", END)

    return graph.compile()