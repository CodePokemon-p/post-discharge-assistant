"""
Every function here is a NODE in the graph. Each one takes the current
state and returns a dict of the fields it wants to update -- LangGraph
merges that dict back into the shared state automatically.

This is the consolidated version: all four LLM-backed nodes (extraction,
intent classification, triage, answering) are live, routed through
graph.llm_provider.call_structured so they work with whichever provider
LLM_PROVIDER in .env points to. Persistence (storage.py) is wired into
extract_care_plan (saves the care plan the moment it's extracted) and
both terminal nodes (logs every check-in, escalated or not).
"""


def extract_care_plan(state: dict) -> dict:
    print("[extract_care_plan] Reading discharge summary...")
    from graph.extraction import extract_care_plan_real
    from storage import save_care_plan

    real_plan = extract_care_plan_real(state["discharge_text"])
    save_care_plan(
        state["patient_id"],
        real_plan,
        language=state.get("language", "en"),
        phone_number=state.get("phone_number"),
    )
    return {"care_plan": real_plan}


def classify_intent(state: dict) -> dict:
    print("[classify_intent] Classifying intent...")
    from graph.intent import classify_intent_real

    result = classify_intent_real(state["patient_message"])
    return {"intent": result.intent}


def answer_question(state: dict) -> dict:
    print("[answer_question] Generating grounded answer...")
    from graph.answering import answer_question_real

    result = answer_question_real(
        state["patient_message"], state["care_plan"],
        state["discharge_text"], state["language"],
    )
    return {"answer": result.answer, "grounded": result.grounded}


def triage_symptom(state: dict) -> dict:
    print("[triage_symptom] Assessing symptom risk...")
    from graph.triage import triage_symptom_real

    result = triage_symptom_real(state["patient_message"], state["care_plan"])
    return {"risk_level": result.risk_level, "triage_reasoning": result.reasoning}


def escalate(state: dict) -> dict:
    print("[escalate] Sending alert to nurse dashboard with full context.")
    from storage import log_message

    reasoning = state.get("triage_reasoning")
    if reasoning is None and state.get("grounded") is False:
        reasoning = (
            "Answer could not be confidently grounded in the discharge "
            "document; escalated for human review."
        )

    log_message(
        patient_id=state["patient_id"],
        content=state["patient_message"],
        intent=state.get("intent"),
        risk_level=state.get("risk_level"),
        answer=state.get("answer"),
        escalated=True,
        reasoning=reasoning,
    )
    return {"escalated": True}


def log_normal(state: dict) -> dict:
    print("[log_normal] Logged as normal, continuing routine check-ins.")
    from storage import log_message

    log_message(
        patient_id=state["patient_id"],
        content=state["patient_message"],
        intent=state.get("intent"),
        risk_level=state.get("risk_level"),
        answer=state.get("answer"),
        escalated=False,
        reasoning=state.get("triage_reasoning"),
    )
    return {"escalated": False}