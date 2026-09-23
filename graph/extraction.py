"""
Real extraction logic, using Claude's tool use (function calling) to
guarantee the response matches CarePlan's schema exactly -- instead of
asking Claude to "please output JSON" and hoping the formatting holds up.

HOW TOOL USE WORKS HERE (read this before using it):
1. We convert the CarePlan Pydantic model into a JSON Schema automatically
   via .model_json_schema() -- so the schema Claude sees and the schema
   Pydantic validates against can NEVER drift out of sync with each other.
2. We pass that schema as a "tool" and force Claude to call it
   (tool_choice), instead of replying with plain text.
3. Claude's reply comes back as a tool_use content block whose .input is
   already a dict matching the schema -- we hand that straight to
   CarePlan(**input) for a final validation pass.

This file is NOT wired into nodes.py yet on purpose. Once your API key is
ready, the swap is one line in nodes.py -- see the comment at the bottom
of this file.
"""

import anthropic

from config import ANTHROPIC_API_KEY, require_api_key
from graph.schemas import CarePlan

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

EXTRACTION_SYSTEM_PROMPT = """You are a clinical document extraction assistant.
You will be given a hospital discharge summary. Extract ONLY the
information that is explicitly stated in the document. Do not infer,
guess, or add anything that isn't written there.

Rules:
- Medications: include every medication listed, with its exact dosing
  schedule as written.
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

CARE_PLAN_TOOL = {
    "name": "record_care_plan",
    "description": "Record the structured care plan extracted from a discharge summary.",
    "input_schema": CarePlan.model_json_schema(),
}


def extract_care_plan_real(discharge_text: str) -> CarePlan:
    """
    Sends discharge_text to Claude and returns a validated CarePlan.
    Raises RuntimeError if no API key is set (via require_api_key), and
    raises a pydantic ValidationError if Claude's output somehow doesn't
    match the schema (rare, since tool_choice forces schema-shaped output,
    but never trust an LLM's output without validating it).
    """
    require_api_key()

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1024,
        system=EXTRACTION_SYSTEM_PROMPT,
        tools=[CARE_PLAN_TOOL],
        tool_choice={"type": "tool", "name": "record_care_plan"},
        messages=[
            {"role": "user", "content": f"Discharge summary:\n\n{discharge_text}"}
        ],
    )

    tool_use_block = next(b for b in response.content if b.type == "tool_use")
    return CarePlan(**tool_use_block.input)


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
# Nothing else in the graph changes -- build.py, the other nodes, and
# main.py all stay exactly as they are. That's the payoff of the
# CarePlan schema and the mock/real split we set up earlier.