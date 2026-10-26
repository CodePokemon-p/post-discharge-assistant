"""
Every function here is a NODE in the graph. Each one takes the current
state and returns a dict of the fields it wants to update -- LangGraph
merges that dict back into the shared state automatically.

Escalation policy:
  * symptom_report + high risk -> escalate (real-time nurse alert)
  * symptom_report + low risk  -> log_normal (no alert)
  * question (grounded or not) -> answered by agent, no alert
  * off_topic                  -> polite decline, no alert

Two entry paths:
  1. Admin intake   -> discharge_text is provided; extract_care_plan runs.
  2. WhatsApp reply -> care_plan is already loaded from DB; extraction is skipped.

Logging rule: log WHAT HAPPENED, never WHAT WAS SAID. No message text,
diagnoses, LLM replies or full phone numbers in logs or in push alerts.
"""

import hashlib
import logging
import re

from safe_logging import mask_phone

logger = logging.getLogger(__name__)


def _safe_id(patient_id) -> str:
    """Mask phone-number IDs; leave non-phone IDs (e.g. patient_001) alone."""
    pid = str(patient_id or "unknown")
    if len(re.sub(r"\D", "", pid)) >= 7:
        return mask_phone(pid)
    return pid


# Module-level cache: hash(discharge_text) -> CarePlan.
# Only used by the admin-intake path. WhatsApp path passes care_plan in
# state and this cache is never touched.
_CARE_PLAN_CACHE: dict[str, object] = {}


def load_history(state: dict) -> dict:
    """Fetch the patient's recent message history before any LLM call."""
    from storage import get_recent_messages
    history = get_recent_messages(state["patient_id"], limit=20, days=7)
    logger.info("load_history loaded=%d prior messages", len(history))
    return {"conversation_history": history}


def extract_care_plan(state: dict) -> dict:
    """
    If care_plan is already in state (WhatsApp path, loaded from DB),
    do nothing -- we don't need to extract again.
    If discharge_text is provided and no care_plan exists yet
    (admin intake path), extract and save it.
    """
    if state.get("care_plan") is not None:
        logger.info("extract_care_plan skipped: care plan already loaded")
        return {}

    doc = state.get("discharge_text", "")
    if not doc:
        logger.info("extract_care_plan skipped: no care plan and no discharge text")
        return {}

    key = hashlib.sha256(doc.encode("utf-8")).hexdigest()
    cached = _CARE_PLAN_CACHE.get(key)
    if cached is not None:
        logger.info("extract_care_plan using cached plan (same document)")
        return {"care_plan": cached}

    logger.info("extract_care_plan reading discharge summary")
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
    logger.info("classify_intent started")
    from graph.intent import classify_intent_real
    result = classify_intent_real(
        state["patient_message"],
        history=state.get("conversation_history", []),
    )
    logger.info("classify_intent done intent=%s", result.intent)
    return {"intent": result.intent}


def answer_question(state: dict) -> dict:
    logger.info("answer_question started")
    from graph.answering import answer_question_real
    from storage import log_message

    result = answer_question_real(
        state["patient_message"],
        state["care_plan"],
        state["discharge_text"],
        state["language"],
        history=state.get("conversation_history", []),
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
    logger.info(
        "answer_question done source=%s grounded=%s",
        result.get("source", "unknown"), result["grounded"],
    )

    return {
        "answer": result["answer"],
        "grounded": result["grounded"],
        "escalated": False,
    }


def decline_off_topic(state: dict) -> dict:
    logger.info("decline_off_topic: politely declining non-medical question")
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

    return {"answer": reply, "grounded": True, "escalated": False}


def triage_symptom(state: dict) -> dict:
    logger.info("triage_symptom started")
    from graph.triage import triage_symptom_real
    result = triage_symptom_real(
        state["patient_message"],
        state["care_plan"],
        history=state.get("conversation_history", []),
    )
    logger.info("triage_symptom done risk=%s", result.risk_level)
    return {"risk_level": result.risk_level, "triage_reasoning": result.reasoning}


def escalate(state: dict) -> dict:
    logger.info(
        "escalate: urgent case logged to dashboard patient=%s",
        _safe_id(state.get("patient_id")),
    )
    from storage import log_message
    from pushover_notify import send_pushover_notification

    log_message(
        patient_id=state["patient_id"],
        content=state["patient_message"],
        intent=state.get("intent"),
        risk_level=state.get("risk_level"),
        answer=state.get("answer"),
        escalated=True,
        reasoning=state.get("triage_reasoning"),
    )

    # The push alert goes through a third party, so it carries no symptom
    # text and no full phone number. Details are on the (password-protected)
    # dashboard.
    send_pushover_notification(
        title=f"URGENT: patient {_safe_id(state.get('patient_id'))}",
        message="High-risk symptom reported. Open the nurse dashboard for details.",
    )

    return {"escalated": True}


def log_normal(state: dict) -> dict:
    logger.info("log_normal: low risk, logged, no nurse alert")
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