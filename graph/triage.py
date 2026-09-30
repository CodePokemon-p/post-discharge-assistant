"""
Hybrid triage: rules first, LLM second. Routed through
graph.llm_provider.call_structured for the LLM fallback, same as
extraction.py -- provider-agnostic, no vendor SDK imported here directly.
"""

from typing import List, Literal

from pydantic import BaseModel, Field

from answering import ANSWER_SYSTEM_PROMPT, ANSWER_TOOL_DESCRIPTION, ANSWER_TOOL_NAME, AnswerResult
from config import require_api_key
from graph.llm_provider import call_structured
from graph.schemas import CarePlan


class TriageResult(BaseModel):
    risk_level: Literal["low", "high"]
    matched_red_flags: List[str] = Field(
        default_factory=list,
        description="Which of the care plan's red flags this message relates to, if any",
    )
    reasoning: str = Field(
        description="One or two sentence clinical reasoning for this risk level"
    )


TRIAGE_SYSTEM_PROMPT = """You are a post-discharge triage assistant.

You will be given a patient's diagnosis, their specific red-flag warning
signs, and a message they sent describing how they're feeling. Decide
whether this message represents a HIGH risk situation needing immediate
nurse review, or a LOW risk situation that's a normal part of recovery.

Err on the side of caution: if the message is ambiguous or you are
unsure, choose HIGH risk. It is far better for a nurse to review a
message that turns out to be nothing, than to miss something that
turns out to matter. This asymmetry is intentional.
"""

TRIAGE_TOOL_NAME = "record_triage_result"
TRIAGE_TOOL_DESCRIPTION = "Record the triage assessment for a patient's message."


def rule_based_check(patient_message: str, red_flags: List[str]) -> List[str]:
    """
    Deterministic first pass, no API call. Lowercases BOTH sides of the
    comparison -- real extracted red flags come back Title-Cased from
    the actual document text, not hand-typed lowercase strings, so this
    must not assume a particular casing on either input.
    """
    msg = patient_message.lower()
    return [flag for flag in red_flags if flag.split()[0].lower() in msg]


def answer_question_real(
    patient_message: str,
    care_plan: CarePlan,
    discharge_text: str,
    language: Literal["en", "ur"],
    history: list = None,
) -> dict:
    require_api_key()

    history_block = ""
    if history:
        lines = [f"- Patient: {h['content']}" + (f" | Agent: {h['answer']}" if h.get('answer') else "")
                 for h in history[-5:]]
        history_block = "Recent conversation:\n" + "\n".join(lines) + "\n\n"

    raw = call_structured(
        system_prompt=ANSWER_SYSTEM_PROMPT.format(
            language="English" if language == "en" else "Urdu"
        ),
        tool_name=ANSWER_TOOL_NAME,
        tool_description=ANSWER_TOOL_DESCRIPTION,
        input_schema=AnswerResult.model_json_schema(),
        user_content=(
            f"Full discharge summary:\n{discharge_text}\n\n"
            f"{history_block}"
            f'Current question: "{patient_message}"'
        ),
    )
    # rest unchanged
    return TriageResult(**raw)


# --- Swapping this into the graph ---
# In nodes.py, replace triage_symptom's body with:
#
#     from graph.triage import triage_symptom_real
#     result = triage_symptom_real(state["patient_message"], state["care_plan"])
#     return {"risk_level": result.risk_level, "triage_reasoning": result.reasoning}