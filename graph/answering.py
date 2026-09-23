"""
Answer agent: RAG-grounded, plain-language answers to patient questions.

WHY "CONTEXT STUFFING" INSTEAD OF A VECTOR DATABASE:
"RAG" usually implies embedding many documents and retrieving only the
top-k relevant chunks. That's the right tool when you're searching across
a large document collection. Here, a single discharge summary is short
enough to fit entirely in Claude's context window, so we skip retrieval
and hand over the whole document directly. This is still grounded
generation in the sense that matters -- the answer is constrained to
what's actually in the document, not the model's general knowledge.
If this system grows to search a patient's full multi-visit history,
THAT's when a real vector store earns its complexity. Don't add
infrastructure before you need it.
"""

from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from config import ANTHROPIC_API_KEY, require_api_key
from graph.schemas import CarePlan

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


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

ANSWER_TOOL = {
    "name": "record_answer",
    "description": "Record the answer to the patient's question.",
    "input_schema": AnswerResult.model_json_schema(),
}


def answer_question_real(
    patient_message: str,
    care_plan: CarePlan,
    discharge_text: str,
    language: Literal["en", "ur"],
) -> AnswerResult:
    require_api_key()

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=512,
        system=ANSWER_SYSTEM_PROMPT.format(
            language="English" if language == "en" else "Urdu"
        ),
        tools=[ANSWER_TOOL],
        tool_choice={"type": "tool", "name": "record_answer"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Full discharge summary:\n{discharge_text}\n\n"
                    f'Patient\'s question: "{patient_message}"'
                ),
            }
        ],
    )

    tool_use_block = next(b for b in response.content if b.type == "tool_use")
    return AnswerResult(**tool_use_block.input)


# --- Swapping this into the graph, once your API key is ready ---
# In nodes.py, replace answer_question's body with:
#
#     from graph.answering import answer_question_real
#     result = answer_question_real(
#         state["patient_message"], state["care_plan"],
#         state["discharge_text"], state["language"],
#     )
#     return {"answer": result.answer, "grounded": result.grounded}
#
# This one also needs a small change in build.py, since an ungrounded
# answer should now escalate instead of ending the graph -- see the note
# below in this file's matching build.py instructions.