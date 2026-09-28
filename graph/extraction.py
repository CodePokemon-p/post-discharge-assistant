"""
Real extraction logic. Now routed through graph.llm_provider.call_structured
instead of calling the Anthropic SDK directly -- see llm_provider.py for why.

Two-layer resilience:
1. Primary path uses FlatCarePlan (Groq-compatible, medications as strings).
2. If Groq's strict validator still rejects the tool call (model returned
   malformed medications), retry once with a MinimalCarePlan schema that
   literally cannot fail validation.

The point: a good prompt reduces failures; code handles the failures that
still happen. Prompts alone cannot make an LLM deterministic.
"""

from pydantic import BaseModel

from config import require_api_key
from graph.llm_provider import call_structured
from graph.schemas import CarePlan, FlatCarePlan, Medication


EXTRACTION_SYSTEM_PROMPT = """You are a clinical document extraction assistant.
You will be given a hospital discharge summary. Extract ONLY the
information that is explicitly stated in the document. Do not infer,
guess, or add anything that isn't written there.

Rules:
- Medications: include EVERY medication mentioned anywhere in the text.
  Return each medication as a single pipe-separated string in this exact
  form: "<name> | <dose> | <schedule>". Example:
      "Ibuprofen | 400mg | every 8 hours as needed for pain"
  Before finalizing, count how many distinct medications are named in
  the source text and verify your medications list contains that same
  number of entries. Never omit a medication just because its dosing
  information is incomplete or unclear -- include it with whatever
  partial information is available rather than dropping it entirely.
- follow_up_date: use the exact date given for the next appointment. If
  no follow-up date is stated, use an empty string.
- red_flags: extract the specific warning signs the document tells the
  patient to watch for and report. These are what your downstream triage
  system will compare the patient's symptoms against, so precision here
  matters more than completeness.

If you are not confident about a field, prefer leaving it empty over
guessing. A missing field can be caught by a human reviewer; a wrong
field could give a patient incorrect medical guidance. A DROPPED
medication is different from a missing field -- it means the patient's
actual prescription is incomplete in our system, which is worse than a
field being empty. Never let an incomplete field cause you to omit an
entire medication.
"""


TRANSCRIPTION_PROMPT = """This image is a hospital discharge summary or
prescription. Transcribe EVERY piece of visible text exactly as
written -- diagnosis, medications, dosages, dates, instructions,
warning signs, doctor's notes, everything. Preserve the original
structure and wording. Do not summarize, do not omit anything as
"unimportant," and do not add anything that isn't visibly written.
If any text is illegible, write [illegible] in its place rather than
guessing what it might say.

Output ONLY the transcribed text itself, as plain text with no markdown
formatting -- no asterisks, no bold, no italics, no headers. The
original document has no such styling; adding it would misrepresent
what's actually printed on the page. Do not add any preamble,
commentary, explanation, or introduction like "Here is the
transcription" -- your entire response must be the document's text
and nothing else, since it will be fed directly into another system
as if it were the document itself."""


def _parse_medication_string(raw: str) -> Medication:
    """
    Convert a flat "Name | dose | schedule" string into a Medication object.
    Handles messy LLM output gracefully -- if the pipe separators aren't
    present, keep the whole string as the name and mark the schedule as
    'as directed' so downstream code never crashes on a partial parse.
    """
    parts = [p.strip() for p in raw.split("|")]

    if len(parts) >= 3:
        return Medication(name=parts[0], schedule=f"{parts[1]}, {parts[2]}")
    elif len(parts) == 2:
        return Medication(name=parts[0], schedule=parts[1])
    else:
        return Medication(name=raw.strip(), schedule="as directed by physician")


def _extract_with_retry(discharge_text: str) -> dict:
    """
    Primary extraction using FlatCarePlan. If Groq's strict validator
    rejects the tool call (which happens when the model returns
    medications in an unexpected shape), retry once with a schema so
    minimal it cannot fail validation.

    Only retries on validation failures -- network errors, auth errors,
    and rate limits still raise immediately, which is correct behavior.
    """
    try:
        return call_structured(
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            tool_name="record_care_plan",
            tool_description=(
                "Record the structured care plan extracted from a "
                "discharge summary."
            ),
            input_schema=FlatCarePlan.model_json_schema(),
            user_content=f"Discharge summary:\n\n{discharge_text}",
        )
    except Exception as e:
        if "tool call validation failed" not in str(e):
            raise
        print(
            "[extract_care_plan] Primary call rejected by Groq's validator; "
            "retrying with minimal schema..."
        )

    # Fallback: schema so loose that validation cannot fail.
    class MinimalCarePlan(BaseModel):
        diagnosis: str
        medications: list[str]
        follow_up_date: str
        red_flags: list[str]

    return call_structured(
        system_prompt=EXTRACTION_SYSTEM_PROMPT,
        tool_name="record_care_plan",
        tool_description=(
            "Record the structured care plan extracted from a discharge summary."
        ),
        input_schema=MinimalCarePlan.model_json_schema(),
        user_content=f"Discharge summary:\n\n{discharge_text}",
    )


def extract_care_plan_real(discharge_text: str) -> CarePlan:
    """
    Sends discharge_text to the configured LLM provider and returns a
    validated rich CarePlan.

    Uses a two-layer strategy:
    1. _extract_with_retry handles malformed LLM responses.
    2. A generic safety-net applies default red flags if the document
       doesn't contain any (some real discharge summaries don't).

    The provider (Groq today, Claude tomorrow) is controlled entirely
    by the LLM_PROVIDER env var.
    """
    require_api_key()

    raw = _extract_with_retry(discharge_text)
    flat = FlatCarePlan(**raw)
    medications = [_parse_medication_string(m) for m in flat.medications]

    care_plan = CarePlan(
        diagnosis=flat.diagnosis,
        medications=medications,
        follow_up_date=flat.follow_up_date,
        red_flags=flat.red_flags,
    )

    if not care_plan.red_flags:
        # SAFETY NET, NOT A SUBSTITUTE FOR REAL CLINICAL REVIEW:
        # Some real discharge summaries (confirmed via testing against
        # an actual document) don't include an explicit "watch for"
        # section at all. Without this fallback, triage_symptom would
        # have nothing to check a patient's message against -- a silent
        # safety gap, not a graceful degradation. These six are
        # near-universal "seek care now" signs used across virtually all
        # discharge protocols regardless of diagnosis. A floor, not a
        # replacement for diagnosis-specific red flags a clinician
        # should still review and expand for real deployment.
        print(
            "[extract_care_plan] WARNING: no red flags found in document -- "
            "applying generic safety-net red flags instead of leaving this empty."
        )
        care_plan.red_flags = [
            "fever",
            "severe pain",
            "difficulty breathing",
            "chest pain",
            "uncontrolled bleeding",
            "confusion or difficulty waking up",
        ]

    return care_plan


def extract_text_from_image(image_base64: str, mime_type: str) -> str:
    """
    Vision call: image -> plain transcribed text. This is a SEPARATE
    step from extraction, not a shortcut that skips it -- the model
    here is only asked to transcribe, not interpret or structure
    anything. That keeps a real photo's messiness (skewed angle, poor
    lighting, handwriting) from being silently "corrected" by the
    model guessing at structure before we've even seen the raw text.
    """
    from graph.llm_provider import call_vision_text
    text = call_vision_text(image_base64, mime_type, TRANSCRIPTION_PROMPT)

    # Defense in depth: even with an explicit instruction not to, some
    # models still prepend a conversational line. Strip a leading line
    # if it looks like commentary rather than document content (ends
    # with a colon, or contains phrases like "here is").
    lines = text.strip().split("\n")
    if lines and (
        lines[0].rstrip().endswith(":")
        or "here is" in lines[0].lower()
        or "based on" in lines[0].lower()
    ):
        text = "\n".join(lines[1:]).strip()

    return text


def extract_care_plan_from_image(image_base64: str, mime_type: str) -> CarePlan:
    """
    Full image -> CarePlan pipeline: transcribe the image to text, then
    hand that text to the EXACT SAME extract_care_plan_real() already
    tested all day against clean text input. Nothing about the
    extraction logic itself changes for images -- only how the text
    arrives.
    """
    transcribed_text = extract_text_from_image(image_base64, mime_type)
    return extract_care_plan_real(transcribed_text)


# --- Swapping this into the graph, once your API key is ready ---
# In nodes.py, extract_care_plan currently does:
#
#     mock_plan = CarePlan(...)
#     return {"care_plan": mock_plan}
#
# Replace those two lines with:
#
#     from graph.extraction import extract_care_plan_real
#     real_plan = extract_care_plan_real(state["discharge_text"])
#     return {"care_plan": real_plan}
#
# Which provider actually answers is controlled entirely by the
# LLM_PROVIDER env var -- nothing here or in nodes.py needs to change
# to go from testing on Groq today to running on Claude.