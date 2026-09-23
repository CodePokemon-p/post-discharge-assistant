from typing import TypedDict, Optional, Literal

class DischargeState (TypedDict, total=False):
    """ 
    Shared  state that flows through every node in the graph.
    total=False means that all fields are optional,untill some nodes fill in it.that 
    is normal in a graph where branches populates different fields.
    """

    discharge_text: str  # raw discharge summary text(input)
    care_plan: dict  # structured care plan extracted from discharge txt
    patient_message: str # latest message from patient input


    language: Literal["en","ur",] # patient preferred language.
    intent:  Literal["question","syptoms_report"]
    answer: Optional[str] # plain language replay.set if intent == question

    

    risk_level: Optional[Literal["low", "high"]] # set if intent == syptoms_report.
    escalated: bool # true if this turn was sent to nurse dashboard.