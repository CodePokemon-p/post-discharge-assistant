"""
Hybrid triage: rules first, LLM second. Routed through
graph.llm_provider.call_structured for the LLM fallback, same as
extraction.py -- provider-agnostic, no vendor SDK imported here directly.
"""

from typing import List, Literal

from pydantic import BaseModel, Field

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


def triage_symptom_real(patient_message: str, care_plan: CarePlan) -> TriageResult:
    matched = rule_based_check(patient_message, care_plan.red_flags)
    if matched:
        return TriageResult(
            risk_level="high",
            matched_red_flags=matched,
            reasoning="Matched an explicit red-flag keyword via rule-based check.",
        )

    require_api_key()

    raw = call_structured(
        system_prompt=TRIAGE_SYSTEM_PROMPT,
        tool_name=TRIAGE_TOOL_NAME,
        tool_description=TRIAGE_TOOL_DESCRIPTION,
        input_schema=TriageResult.model_json_schema(),
        user_content=(
            f"Patient's diagnosis: {care_plan.diagnosis}\n"
            f"Red flags to watch for: {care_plan.red_flags}\n\n"
            f'Patient\'s message: "{patient_message}"'
        ),
    )
    return TriageResult(**raw)


# --- Swapping this into the graph ---
# In nodes.py, replace triage_symptom's body with:
#
#     from graph.triage import triage_symptom_real
#     result = triage_symptom_real(state["patient_message"], state["care_plan"])
#     return {"risk_level": result.risk_level, "triage_reasoning": result.reasoning}