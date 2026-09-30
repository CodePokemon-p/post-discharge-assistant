"""
Answer agent, v2 -- three layers now, not one:

1. DOCUMENT-GROUNDED: answer is checked against the discharge doc, AND
   the model must quote the exact source sentence, AND the code
   independently verifies that quote actually appears in the document
   before trusting it. This is what fixes "how do you know it's not
   hallucinating" -- we don't just trust the model's self-reported
   confidence anymore, we verify it in code.

2. GENERAL-GUIDANCE FALLBACK: if the document doesn't cover it, check
   a small curated knowledge base of common, doctor-approved recovery
   guidance (NOT patient-specific, NOT written by this AI or by me --
   see general_guidance.py, which ships with placeholder entries that
   MUST be replaced with real clinically-approved content before any
   real use). This is what stops every off-document question from
   flooding the nurse queue, per the exact gap raised in review.

3. SOFT REPLY: only if neither layer above can answer it. Note the
   wording is deliberately "please check with your care team" -- the
   design only alerts a nurse for high-risk symptoms (see triage.py),
   so this layer must not promise the patient that someone has been
   notified when nobody has.
"""

from typing import Literal

from pydantic import BaseModel, Field

from config import require_api_key
from graph.llm_provider import call_structured
from graph.schemas import CarePlan
from general_guidance import find_general_guidance


class AnswerResult(BaseModel):
    answer: str = Field(
        description="Plain-language answer, or a message telling the patient "
        "to check with their care team"
    )
    grounded: bool = Field(
        description="True only if the discharge information actually supports "
        "this answer."
    )
    source_quote: str = Field(
        default="",
        description="The exact sentence or phrase copied verbatim from the "
        "discharge document that supports this answer. Empty string if "
        "grounded is false. Must be an exact quote, not a paraphrase -- "
        "this is independently checked against the document, so a "
        "paraphrased or invented quote will cause this answer to be "
        "treated as ungrounded even if you set grounded to true.",
    )


ANSWER_SYSTEM_PROMPT = """You are a patient-facing recovery assistant.

You will be given a patient's full discharge summary and a question they
asked. Answer ONLY using information present in the discharge summary.
Never use general medical knowledge to fill gaps -- if the discharge
summary doesn't cover the patient's question, set grounded to false,
source_quote to an empty string, and write an answer telling the patient
to check with their care team, rather than guessing.

If grounded is true, source_quote MUST be an exact, verbatim copy of the
sentence or phrase from the discharge summary that supports your answer
-- not a paraphrase, not a summary. Copy it exactly as written. The
quote must be at least 4 words long; a single word is not evidence.

Write your answer in plain, everyday language -- no medical jargon a
non-clinician wouldn't understand. Respond in the patient's preferred
language: {language}.

A wrong medical answer can cause real harm. When in doubt, say you don't
know and let the care team handle it.
"""

ANSWER_TOOL_NAME = "record_answer"
ANSWER_TOOL_DESCRIPTION = "Record the answer to the patient's question."


def _verify_quote(source_quote: str, discharge_text: str) -> bool:
    """
    Independently checks that source_quote actually appears in the
    document -- normalized for whitespace/case, but still a real
    substring check, not just trusting the model said so. This is the
    concrete answer to "how do you verify it's not hallucinating":
    we don't just ask the model to self-report confidence, we check
    its claimed evidence against the source text in code.

    Requires the quote to be at least 4 words long. A one- or two-word
    "quote" (e.g. "yes") could pass a naive substring check without
    being real evidence, so we reject short quotes as unverifiable.
    """
    quote = source_quote.strip()
    if not quote:
        return False
    if len(quote.split()) < 4:
        return False
    normalize = lambda s: " ".join(s.lower().split())
    return normalize(quote) in normalize(discharge_text)


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
   
    result = AnswerResult(**raw)

    # LAYER 1: verify the model's claimed evidence actually exists in
    # the document -- don't just trust grounded=True at face value.
    if result.grounded and _verify_quote(result.source_quote, discharge_text):
        return {
            "answer": result.answer,
            "grounded": True,
            "source": "document",
            "priority": None,
        }

    # LAYER 2: check the curated general-guidance knowledge base before
    # giving up.
    guidance = find_general_guidance(patient_message)
    if guidance:
        return {
            "answer": guidance,
            "grounded": True,
            "source": "general_guidance",
            "priority": None,
        }

    # LAYER 3: neither the document nor general guidance covers this.
    # Wording is honest: "please check with your care team" rather than
    # "I've flagged it" (which would imply a notification that does not
    # happen in this path).
    return {
        "answer": (
            "Your discharge summary doesn't cover this specific question. "
            "Please check this with your care team."
        ),
        "grounded": False,
        "source": "soft_reply",
        "priority": None,
    }