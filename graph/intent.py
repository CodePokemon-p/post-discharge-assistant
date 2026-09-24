"""
Intent classifier: decides whether a patient's message is a recovery
question or a symptom report. Routed through call_structured, same
pattern as the other three nodes.

WHY THIS REPLACES THE KEYWORD MOCK:
The original mock checked for English words like "pain" and "fever" --
which silently fails on Urdu input, sarcasm, or any phrasing that
doesn't happen to contain those exact words ("my knee feels weird" is
clearly symptom-relevant but contains none of them). An LLM call
understands intent regardless of language or exact phrasing.
"""

from typing import Literal

from pydantic import BaseModel, Field

from config import require_api_key
from graph.llm_provider import call_structured


class IntentResult(BaseModel):
    intent: Literal["question", "symptom_report"]
    reasoning: str = Field(description="One short sentence explaining the classification")


INTENT_SYSTEM_PROMPT = """You are classifying a message from a post-discharge
patient into exactly one of two categories:

- "question": the patient is asking about their care, recovery, diet,
  activity, medications, or anything informational.
- "symptom_report": the patient is describing how they currently feel,
  a symptom, pain level, or a physical change -- anything that
  describes their condition rather than asking about it.

The patient may write in English or Urdu. Classify based on meaning,
not language or exact wording. If a message does both (asks a question
AND describes a symptom), classify it as "symptom_report" -- the
symptom side needs triage first; the care team can address the question
once a human is already involved.
"""

INTENT_TOOL_NAME = "record_intent"
INTENT_TOOL_DESCRIPTION = "Record the classified intent of the patient's message."


def classify_intent_real(patient_message: str) -> IntentResult:
    require_api_key()

    raw = call_structured(
        system_prompt=INTENT_SYSTEM_PROMPT,
        tool_name=INTENT_TOOL_NAME,
        tool_description=INTENT_TOOL_DESCRIPTION,
        input_schema=IntentResult.model_json_schema(),
        user_content=f'Patient\'s message: "{patient_message}"',
    )
    return IntentResult(**raw)


# --- Swapping this into the graph ---
# In nodes.py, replace classify_intent's body with:
#
#     from graph.intent import classify_intent_real
#     result = classify_intent_real(state["patient_message"])
#     return {"intent": result.intent}