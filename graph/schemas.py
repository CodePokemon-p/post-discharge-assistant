from pydantic import BaseModel, Field
from typing import List


class Medication(BaseModel):
    name: str
    schedule: str  # e.g. "every 8 hours"


class CarePlan(BaseModel):
    """
    This is the exact shape Claude will be asked to fill in later, once
    the API key is wired up (via structured output / tool use). Defining
    it now means the extraction prompt we write later has zero ambiguity
    about what "correct" looks like -- and Pydantic will reject anything
    that doesn't match, instead of letting a malformed response silently
    break triage_symptom downstream.
    """
    diagnosis: str
    medications: List[Medication]
    follow_up_date: str  # ISO date string, e.g. "2026-10-05"
    red_flags: List[str] = Field(
        description="Symptoms that should trigger escalation to a nurse"
    )