from typing import TypedDict, Optional, Literal

from graph.schemas import CarePlan


class DischargeState(TypedDict, total=False):
    """
    Shared state that flows through every node in the graph.
    total=False means each field is optional until some node fills it in.
    """
    patient_id: str                 # which patient this check-in belongs to
    discharge_text: str             # raw discharge summary text (input)
    care_plan: CarePlan             # structured, validated care plan
    patient_message: str            # latest message from the patient (input)
    language: Literal["en", "ur"]   # patient's preferred language
    intent: Literal["question", "symptom_report"]
    answer: Optional[str]           # plain-language reply, set if intent == question
    grounded: Optional[bool]        # False if answer couldn't be confidently grounded -> escalate
    risk_level: Optional[Literal["low", "high"]]   # set if intent == symptom_report
    triage_reasoning: Optional[str] # why triage_symptom made its risk_level call
    escalated: bool                 # True if this turn was sent to the nurse dashboard

    