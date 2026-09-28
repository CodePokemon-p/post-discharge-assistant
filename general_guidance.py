"""
General recovery guidance lookup.

CRITICAL: The entries below are PLACEHOLDERS demonstrating the architecture.
Real medical content must be reviewed and approved by a licensed clinician
before this system touches real patients. This module exists so the
answering pipeline has somewhere to look before escalating a common
question to a nurse -- it is NOT a substitute for clinical approval.
"""

from typing import Optional


# Keys are lowercase substrings matched against the patient's question.
# Values are the approved guidance that the answer node can quote.
_GENERAL_GUIDANCE = {
    "eat": "Resume a normal diet as tolerated unless your discharge "
           "instructions say otherwise. Start with light meals and "
           "gradually return to regular eating.",
    "diet": "Follow the diet instructions in your discharge summary. "
            "If none were given, resume a normal diet as tolerated.",
    "shower": "Keep the incision site clean and dry. Follow the wound "
              "care instructions in your discharge summary. If it does "
              "not mention showering, confirm with your care team first.",
    "bath": "Avoid soaking the incision until cleared by your care "
            "team. Sponge baths are usually safe if the wound stays dry.",
    "drive": "Do not drive until cleared by your surgeon, especially if "
             "you are taking prescription pain medication.",
    "exercise": "Gentle walking is usually encouraged. Avoid heavy "
                "lifting and strenuous activity until your follow-up "
                "visit. Follow your discharge instructions for details.",
    "work": "Return-to-work timing depends on your procedure and job. "
            "Confirm with your care team at your follow-up appointment.",
    "alcohol": "Avoid alcohol while taking prescription medications, "
               "especially pain relievers and antibiotics.",
    "smoking": "Avoid smoking -- it slows wound healing and increases "
               "complications after surgery.",
    "sleep": "Rest is important during recovery. If you have trouble "
             "sleeping or notice new fatigue, mention it to your care team.",
}


def find_general_guidance(question: str) -> Optional[str]:
    """
    Return general recovery guidance for a question, or None if the
    question is not covered by the approved list above.

    Matching is intentionally simple: lowercase substring match against
    the keys. Any real deployment should replace this with a
    clinician-curated knowledge base.
    """
    q = question.lower()
    for keyword, guidance in _GENERAL_GUIDANCE.items():
        if keyword in q:
            return guidance
    return None