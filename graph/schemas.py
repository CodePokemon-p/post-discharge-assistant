from pydantic import BaseModel, Field
from typing import List


class Medication(BaseModel):
    name: str
    schedule: str  # e.g. "every 8 hours"


class CarePlan(BaseModel):
    """
    Domain model for a structured care plan. This is the shape the rest
    of the graph (triage, logging, escalation) works with.
    """
    diagnosis: str
    medications: List[Medication]
    follow_up_date: str  # ISO date string, e.g. "2026-10-05"
    red_flags: List[str] = Field(
        description="Symptoms that should trigger escalation to a nurse"
    )


class FlatCarePlan(BaseModel):
    """
    Groq-compatible flat schema used ONLY for the LLM extraction call.
    Groq's tool-calling validator rejects nested arrays of objects
    (it returned "expected object, but got string" for medications),
    so medications are flattened to plain strings here. After extraction,
    graph/extraction.py parses them back into Medication objects and
    returns the rich CarePlan above.

    When you switch to Anthropic Claude (which supports nested schemas),
    you can pass CarePlan.model_json_schema() directly and skip the
    conversion step -- the graph code never has to change.
    """
    diagnosis: str
    medications: List[str]  # e.g. "Ibuprofen | 400mg | every 8 hours"
    follow_up_date: str
    red_flags: List[str]