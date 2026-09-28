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

Care-plan caching: extraction is expensive and non-deterministic, so we
extract once per unique document and reuse the result for every message
in the same session. Keyed by document hash -- different documents do
not collide.
"""

import hashlib


# Module-level cache: hash(discharge_text) -> CarePlan.
# Cleared on process restart, which is fine for a single-session demo.
# A production version would persist this in storage.py and load it by
# patient_id instead.
_CARE_PLAN_CACHE: dict[str, object] = {}


def extract_care_plan(state: dict) -> dict:
    doc = state["discharge_text"]
    key = hashlib.sha256(doc.encode("utf-8")).hexdigest()

    cached = _CARE_PLAN_CACHE.get(key)
    if cached is not None:
        print("[extract_care_plan] Using cached care plan (same document).")
        return {"care_plan": cached}

    print("[extract_care_plan] Reading discharge summary...")
    from graph.extraction import extract_care_plan_real
    from storage import save_care_plan

    real_plan = extract_care_plan_real(doc)
    save_care_plan(
        state["patient_id"],
        real_plan,
        language=state.get("language", "en"),
        phone_number=state.get("phone_number"),
    )

    _CARE_PLAN_CACHE[key] = real_plan
    return {"care_plan": real_plan}


def classify_intent(state: dict) -> dict:
    print("[classify_intent] Classifying intent...")
    from graph.intent import classify_intent_real

    result = classify_intent_real(state["patient_message"])
    return {"intent": result.intent}


def answer_question(state: dict) -> dict:
    """
    Three-layer answer pipeline. NEVER triggers a real-time nurse alert.
    Ungrounded answers get a soft reply, not an alert.
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
        "escalated": False,
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