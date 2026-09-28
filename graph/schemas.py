from pydantic import BaseModel, Field
from typing import List


class Medication(BaseModel):
    name: str
    schedule: str = Field(
        default="Not clearly specified in document -- verify with care team",
        description="Dosage, frequency, and any instructions, exactly as "
        "written. If the document's wording doesn't map cleanly to a "
        "clear schedule, put whatever partial dosing text IS available "
        "here rather than omitting the field entirely.",
    )


class CarePlan(BaseModel):
    """
    Domain model for a structured care plan. This is what the rest of
    the graph works with -- triage, logging, escalation.
    """
    diagnosis: str
    medications: List[Medication]
    follow_up_date: str
    red_flags: List[str] = Field(
        description="Symptoms that should trigger escalation to a nurse"
    )


class FlatCarePlan(BaseModel):
    """
    Groq-compatible flat schema used ONLY for the LLM extraction call.
    Groq's tool-calling validator rejects nested arrays of objects, so
    medications are flattened to strings here and converted back into
    Medication objects in graph/extraction.py.

    When you switch to Anthropic Claude (which supports nested schemas),
    you can pass CarePlan.model_json_schema() directly and skip the
    conversion step.
    """
    diagnosis: str
    medications: List[str]
    follow_up_date: str
    red_flags: List[str]