"""
Hybrid triage: rules first, LLM second.

WHY THIS SHAPE MATTERS (read before using):
1. rule_based_check() runs first and is pure Python -- no API call, no
   cost, no latency, and critically: no way for clever phrasing to talk
   it out of escalating. If a patient's message contains a known red
   flag keyword, this ALWAYS escalates. That's your safety floor.
2. Claude only gets involved for the genuinely ambiguous middle ground --
   messages that don't contain an exact red-flag word but might still
   describe something concerning ("it's way worse than yesterday and I
   can barely put weight on it"). That's exactly the kind of nuance
   keyword matching alone can't catch.
3. The system prompt is deliberately biased toward escalating when
   unsure. In a medical context, a false alarm costs a nurse five
   minutes; a missed one costs much more. Asymmetric risk means the
   prompt should be asymmetric too.

Like extraction.py, this is NOT wired into nodes.py yet -- see the swap
instructions at the bottom.
"""

from typing import List, Literal

import anthropic
from pydantic import BaseModel, Field

from config import ANTHROPIC_API_KEY, require_api_key
from graph.schemas import CarePlan

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


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
turns out to matter. This asymmetry is intentional -- do not try to
balance false alarms against missed warnings as if they were equally
costly. They are not.
"""

TRIAGE_TOOL = {
    "name": "record_triage_result",
    "description": "Record the triage assessment for a patient's message.",
    "input_schema": TriageResult.model_json_schema(),
}


def rule_based_check(patient_message: str, red_flags: List[str]) -> List[str]:
    """
    Deterministic first pass, no API call. Returns the list of red flags
    that were matched (empty list = no rule-based match, fall through to
    the LLM). Kept deliberately simple and literal -- this is the layer
    that must never be "clever," only reliable.
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

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=512,
        system=TRIAGE_SYSTEM_PROMPT,
        tools=[TRIAGE_TOOL],
        tool_choice={"type": "tool", "name": "record_triage_result"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Patient's diagnosis: {care_plan.diagnosis}\n"
                    f"Red flags to watch for: {care_plan.red_flags}\n\n"
                    f'Patient\'s message: "{patient_message}"'
                ),
            }
        ],
    )

    tool_use_block = next(b for b in response.content if b.type == "tool_use")
    return TriageResult(**tool_use_block.input)


# --- Swapping this into the graph, once your API key is ready ---
# In nodes.py, triage_symptom currently does inline keyword matching.
# Replace its body with:
#
#     from graph.triage import triage_symptom_real
#     result = triage_symptom_real(state["patient_message"], state["care_plan"])
#     return {"risk_level": result.risk_level}
#
# Worth doing right away too: add "triage_reasoning" as a field on
# DischargeState and store result.reasoning there. The nurse dashboard
# will want that reasoning shown alongside the alert -- a nurse should
# never have to guess *why* something got escalated.