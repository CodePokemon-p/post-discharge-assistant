"""
Every function here is a NODE in the graph. Each one takes the current
state and returns a dict of the fields it wants to update -- LangGraph
merges that dict back into the shared state automatically.

Right now every node uses simple mock/rule-based logic instead of calling
Claude. This lets us run the WHOLE pipeline end-to-end today and prove the
wiring (the edges and branches) is correct, before any API key exists.
Each docstring says exactly what real logic will replace the mock later.
"""

from graph.schemas import CarePlan, Medication



def extract_care_plan(state: dict) -> dict:
    print("[extract_care_plan] Reading discharge summary...")
    mock_plan = CarePlan(
        diagnosis="Post knee-surgery recovery",
        medications=[Medication(name="Ibuprofen", schedule="every 8 hours")],
        follow_up_date="2026-10-05",
        red_flags=["fever", "severe swelling", "pain above 8/10"],
    )
    return {"care_plan": mock_plan}


def classify_intent(state: dict) -> dict:
    """
    REAL VERSION (later): one LLM call asking Claude to classify the
    patient's message as "question" or "symptom_report".

    MOCK VERSION (now): crude keyword check, just so the conditional
    branch below actually has something to branch on.
    """
    msg = state["patient_message"].lower()
    symptom_words = ["pain", "fever", "swelling", "bleeding", "hurts"]
    intent = "symptom_report" if any(w in msg for w in symptom_words) else "question"
    print(f"[classify_intent] intent = {intent}")
    return {"intent": intent}


def answer_question(state: dict) -> dict:
    """
    REAL VERSION (later): RAG -- retrieve relevant chunks from the
    patient's care_plan / discharge doc (vector store), then ask Claude
    to answer in plain language, in state['language']. If retrieval finds
    nothing relevant, the answer must say so and trigger escalation instead
    of guessing.

    MOCK VERSION (now): fixed placeholder answer.
    """
    print("[answer_question] Generating grounded answer (mock)...")
    return {"answer": "(placeholder) Based on your discharge plan, here's the answer..."}


def triage_symptom(state: dict) -> dict:
    msg = state["patient_message"].lower()
    red_flags = state["care_plan"].red_flags     # was: state["care_plan"].get("red_flags", [])
    high_risk = any(flag.split()[0] in msg for flag in red_flags)
    risk = "high" if high_risk else "low"
    print(f"[triage_symptom] risk_level = {risk}")
    return {"risk_level": risk}


def escalate(state: dict) -> dict:
    """
    REAL VERSION (later): write an alert row to the nurse dashboard's DB
    with full conversation context, and optionally notify via Twilio.
    """
    print("[escalate] Sending alert to nurse dashboard with full context.")
    return {"escalated": True}


def log_normal(state: dict) -> dict:
    """
    REAL VERSION (later): log this check-in as normal, let the scheduler
    queue the next routine check-in.
    """
    print("[log_normal] Logged as normal, continuing routine check-ins.")
    return {"escalated": False}