"""
Real extraction logic. Routes through graph.llm_provider.call_structured.

Uses FlatCarePlan for the LLM call (Groq-compatible) and converts the
result into the rich CarePlan domain model afterwards, so downstream
nodes (triage, logging, escalation) always see proper Medication objects.

When you switch to Anthropic Claude, change the input_schema back to
CarePlan.model_json_schema() and drop the conversion step -- nothing
else in the codebase needs to change.
"""

from config import require_api_key
from graph.llm_provider import call_structured
from graph.schemas import CarePlan, Medication, FlatCarePlan

EXTRACTION_SYSTEM_PROMPT = """You are a clinical document extraction assistant.
You will be given a hospital discharge summary. Extract ONLY the
information that is explicitly stated in the document. Do not infer,
guess, or add anything that isn't written there.

Rules:
- Medications: include every medication listed. Format each medication
  as a single string in this exact form:
      "<name> | <dose> | <schedule>"
  Example: "Ibuprofen | 400mg | every 8 hours as needed for pain"
  Do NOT return medications as objects or as multi-sentence prose --
  return each one as a single pipe-separated string.
- follow_up_date: use the exact date given for the next appointment. If
  no follow-up date is stated, use an empty string.
- red_flags: extract the specific warning signs the document tells the
  patient to watch for and report. These are what your downstream triage
  system will compare the patient's symptoms against, so precision here
  matters more than completeness.

If you are not confident about a field, prefer leaving it empty over
guessing. A missing field can be caught by a human reviewer; a wrong
field could give a patient incorrect medical guidance.
"""


def _parse_medication_string(raw: str) -> Medication:
    """
    Convert a flat "Name | dose | schedule" string into a Medication object.
    Handles messy LLM output gracefully -- if the pipe separators aren't
    present, keep the whole string as the name and mark the schedule as
    'as directed' so downstream code never crashes on a partial parse.
    """
    parts = [p.strip() for p in raw.split("|")]

    if len(parts) >= 3:
        # Combine dose + schedule into the schedule field for readability
        return Medication(name=parts[0], schedule=f"{parts[1]}, {parts[2]}")
    elif len(parts) == 2:
        return Medication(name=parts[0], schedule=parts[1])
    else:
        return Medication(name=raw.strip(), schedule="as directed by physician")


def extract_care_plan_real(discharge_text: str) -> CarePlan:
    """
    Sends discharge_text to the configured LLM provider and returns a
    validated rich CarePlan.

    Note: require_api_key() only enforces the check when
    LLM_PROVIDER=anthropic; see config.py.
    """
    require_api_key()

    raw = call_structured(
        system_prompt=EXTRACTION_SYSTEM_PROMPT,
        tool_name="record_care_plan",
        tool_description="Record the structured care plan extracted from a discharge summary.",
        input_schema=FlatCarePlan.model_json_schema(),
        user_content=f"Discharge summary:\n\n{discharge_text}",
    )

    flat = FlatCarePlan(**raw)

    # Convert flat medication strings into rich Medication objects
    medications = [_parse_medication_string(m) for m in flat.medications]

    return CarePlan(
        diagnosis=flat.diagnosis,
        medications=medications,
        follow_up_date=flat.follow_up_date,
        red_flags=flat.red_flags,
    )


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