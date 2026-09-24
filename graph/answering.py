"""
Answer agent: RAG-grounded, plain-language answers to patient questions.
Routed through graph.llm_provider.call_structured -- same reasoning as
extraction.py and triage.py, no vendor SDK imported here directly.

WHY "CONTEXT STUFFING" INSTEAD OF A VECTOR DATABASE:
A single discharge summary is short enough to fit entirely in the
model's context window, so we hand over the whole document directly
instead of embedding + retrieving chunks. Still grounded generation in
the sense that matters -- constrained to what's in the document, not
general knowledge. A real vector store earns its complexity only once
you're searching across a patient's full multi-visit history.
"""

from typing import Literal

from pydantic import BaseModel, Field

from config import require_api_key
from graph.llm_provider import call_structured
from graph.schemas import CarePlan


class AnswerResult(BaseModel):
    answer: str = Field(
        description="Plain-language answer, or a message telling the patient "
        "their care team will follow up"
    )
    grounded: bool = Field(
        description="True only if the discharge information actually supports "
        "this answer. False if the question can't be confidently answered from "
        "what's available -- this should trigger escalation, not a guess."
    )


ANSWER_SYSTEM_PROMPT = """You are a patient-facing recovery assistant.

You will be given a patient's full discharge summary and a question they
asked. Answer ONLY using information present in the discharge summary.
Never use general medical knowledge to fill gaps -- if the discharge
summary doesn't cover the patient's question, set grounded to false and
write an answer telling the patient their care team will follow up,
rather than guessing.

Write your answer in plain, everyday language -- no medical jargon a
non-clinician wouldn't understand. Respond in the patient's preferred
language: {language}.

A wrong medical answer can cause real harm. When in doubt, say you don't
know and let the care team handle it.
"""

ANSWER_TOOL_NAME = "record_answer"
ANSWER_TOOL_DESCRIPTION = "Record the answer to the patient's question."


def answer_question_real(
    patient_message: str,
    care_plan: CarePlan,
    discharge_text: str,
    language: Literal["en", "ur"],
) -> AnswerResult:
    require_api_key()

    raw = call_structured(
        system_prompt=ANSWER_SYSTEM_PROMPT.format(
            language="English" if language == "en" else "Urdu"
        ),
        tool_name=ANSWER_TOOL_NAME,
        tool_description=ANSWER_TOOL_DESCRIPTION,
        input_schema=AnswerResult.model_json_schema(),
        user_content=(
            f"Full discharge summary:\n{discharge_text}\n\n"
            f'Patient\'s question: "{patient_message}"'
        ),
    )
    return AnswerResult(**raw)


# --- Swapping this into the graph ---
# In nodes.py, replace answer_question's body with:
#
#     from graph.answering import answer_question_real
#     result = answer_question_real(
#         state["patient_message"], state["care_plan"],
#         state["discharge_text"], state["language"],
#     )
#     return {"answer": result.answer, "grounded": result.grounded}
#
# This also requires the routing change in build.py we discussed earlier
# (route_by_groundedness) -- if you haven't added it yet, do that now too.