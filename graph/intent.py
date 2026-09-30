"""
Intent classifier: decides whether a patient's message is a recovery
question, a symptom report, or off-topic. Routed through call_structured,
same pattern as the other nodes.

WHY THREE CATEGORIES:
The original version only had "question" and "symptom_report". That meant
any non-medical message ("tell me a joke", "what do you think about
OpenAI?") fell into "question", failed the grounding check, and got a
"your discharge summary doesn't cover this" reply. With a dedicated
"off_topic" label, non-medical messages get a polite decline instead,
and never waste an answer-node call or trigger a nurse alert.

WHY AN LLM INSTEAD OF KEYWORDS:
Keyword checks silently fail on Urdu, sarcasm, or phrasing that doesn't
contain exact words ("my knee feels weird" is symptom-relevant but
contains none of the old keywords). An LLM call understands intent
regardless of language or phrasing.
"""

from typing import Literal

from pydantic import BaseModel, Field

from config import require_api_key
from graph.llm_provider import call_structured


class IntentResult(BaseModel):
    intent: Literal["question", "symptom_report", "off_topic"]
    reasoning: str = Field(description="One short sentence explaining the classification")


INTENT_SYSTEM_PROMPT = """You are classifying a message from a post-discharge
patient into exactly one of three categories:

- "question": the patient is asking about their care, recovery, diet,
  activity, medications, wound care, follow-up, or anything informational
  that could plausibly relate to their recovery.
  Examples: "when is my follow-up?", "can I drive?", "can I eat spicy
  food?", "should I keep the bandage on?"

- "symptom_report": the patient is describing how they currently feel,
  a symptom, pain level, or a physical change -- anything that
  describes their condition rather than asking about it.
  Examples: "I have a fever", "my pain is worse today", "I feel dizzy",
  "my leg is swelling".

- "off_topic": the message is NOT about the patient's medical recovery
  at all. It's general chat, opinions, technology, jokes, current events,
  or anything unrelated to their health.
  Examples: "what do you think about OpenAI?", "tell me a joke",
  "who is the president?", "do you like cricket?", "what's the weather?"

The patient may write in English or Urdu. Classify based on meaning,
not language or exact wording. If a message does both (asks a question
AND describes a symptom), classify it as "symptom_report" -- the
symptom side needs triage first; the care team can address the question
once a human is already involved.

Return only the classification and a short reasoning sentence.
"""

INTENT_TOOL_NAME = "record_intent"
INTENT_TOOL_DESCRIPTION = "Record the classified intent of the patient's message."


def classify_intent_real(patient_message: str, history: list = None) -> IntentResult:
    require_api_key()

    history_block = ""
    if history:
        lines = [f"- {h['content']}" for h in history[-5:]]
        history_block = "Recent patient messages:\n" + "\n".join(lines) + "\n\n"

    raw = call_structured(
        system_prompt=INTENT_SYSTEM_PROMPT,
        tool_name=INTENT_TOOL_NAME,
        tool_description=INTENT_TOOL_DESCRIPTION,
        input_schema=IntentResult.model_json_schema(),
        user_content=f"{history_block}Current message: \"{patient_message}\"",
    )
    return IntentResult(**raw)

# --- Swapping this into the graph ---
# In nodes.py, replace classify_intent's body with:
#
#     from graph.intent import classify_intent_real
#     result = classify_intent_real(state["patient_message"])
#     return {"intent": result.intent}