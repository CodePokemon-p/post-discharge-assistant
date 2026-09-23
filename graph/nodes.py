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
    """
    REAL VERSION (now): sends the discharge summary to the configured LLM
    provider and returns a validated CarePlan object.

    The provider (Groq today, Claude tomorrow) is controlled entirely by
    the LLM_PROVIDER env var -- this function doesn't know or care which.
    """
    print("[extract_care_plan] Reading discharge summary...")
    from graph.extraction import extract_care_plan_real
    real_plan = extract_care_plan_real(state["discharge_text"])
    return {"care_plan": real_plan}

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
    print("[triage_symptom] Assessing symptom risk...")
    from graph.triage import triage_symptom_real
    result = triage_symptom_real(state["patient_message"], state["care_plan"])
    return {"risk_level": result.risk_level, "triage_reasoning": result.reasoning}


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