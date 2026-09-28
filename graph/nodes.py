"""
Every function here is a NODE in the graph. Each one takes the current
state and returns a dict of the fields it wants to update -- LangGraph
merges that dict back into the shared state automatically.

Escalation policy (matches mentor feedback):
  * symptom_report + high risk -> escalate (real-time nurse alert)
  * symptom_report + low risk  -> log_normal (no alert)
  * question (grounded or not) -> answered by agent, no alert
  * off_topic                  -> polite decline, no alert

Only high-risk symptoms interrupt a nurse in real time. Everything
else is either answered or logged for batch review.
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
    """
    Three-layer answer pipeline. NEVER triggers a real-time nurse alert.
    If the answer can't be grounded, the agent gives a soft reply and
    the case is logged for batch review (not alerted).
    """
    print("[answer_question] Generating grounded answer...")
    from graph.answering import answer_question_real
    from storage import log_message

    result = answer_question_real(
        state["patient_message"],
        state["care_plan"],
        state["discharge_text"],
        state["language"],
    )

    # Log every answer for audit trail (no nurse alert here regardless)
    log_message(
        patient_id=state["patient_id"],
        content=state["patient_message"],
        intent=state.get("intent"),
        risk_level=None,
        answer=result["answer"],
        escalated=False,
        reasoning=(
            f"source={result.get('source', 'unknown')}; "
            f"grounded={result['grounded']}"
        ),
    )

    return {
        "answer": result["answer"],
        "grounded": result["grounded"],
        "escalated": False,   # <-- answer path NEVER alerts a nurse
    }


def decline_off_topic(state: dict) -> dict:
    """
    Non-medical / unrelated messages get a polite decline.
    They do NOT hit the nurse dashboard.
    """
    print("[decline_off_topic] Politely declining non-medical question.")
    from storage import log_message

    reply = (
        "I can only help with questions about your recovery after "
        "discharge. For anything else, please reach out to your "
        "care team directly."
    )

    log_message(
        patient_id=state["patient_id"],
        content=state["patient_message"],
        intent=state.get("intent"),
        risk_level=None,
        answer=reply,
        escalated=False,
        reasoning="off_topic -- declined, no nurse alert.",
    )

    return {
        "answer": reply,
        "grounded": True,
        "escalated": False,
    }


def triage_symptom(state: dict) -> dict:
    print("[triage_symptom] Assessing symptom risk...")
    from graph.triage import triage_symptom_real

    result = triage_symptom_real(state["patient_message"], state["care_plan"])
    return {
        "risk_level": result.risk_level,
        "triage_reasoning": result.reasoning,
    }


def escalate(state: dict) -> dict:
    """
    ONLY node that alerts a nurse in real time. Reserved for high-risk
    symptom reports.
    """
    print("[escalate] URGENT -- sending alert to nurse dashboard with full context.")
    from storage import log_message

    log_message(
        patient_id=state["patient_id"],
        content=state["patient_message"],
        intent=state.get("intent"),
        risk_level=state.get("risk_level"),
        answer=state.get("answer"),
        escalated=True,
        reasoning=state.get("triage_reasoning"),
    )
    return {"escalated": True}


def log_normal(state: dict) -> dict:
    """
    Low-risk symptom report or routine check-in. Logged, no nurse alert.
    """
    print("[log_normal] Low-risk -- logged, no nurse alert.")
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