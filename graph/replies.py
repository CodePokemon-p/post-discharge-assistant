"""
Everything the assistant says to a patient, in ONE place.

Why one file: a nurse can read and edit the wording without touching any logic,
and tests can check every sentence for safety and readability.

These replies follow health-literacy guidance for patient materials:
  * plain words and short sentences (aim: about a 6th-grade reading level)
  * one idea per paragraph
  * say what to do next, and when to get urgent help
  * never diagnose, never tell a patient to stop or change a medicine,
    never promise what the care team will do

NOTE FOR REVIEWERS: the wording here is a first draft written for clarity and
safety. A nurse or doctor should review and edit it before real patients use it.
"""
import re
import zlib
from typing import Optional


def _pick(options: list[str], seed_text: str) -> str:
    """Stable choice between wordings, so replies do not all sound identical."""
    return options[zlib.crc32((seed_text or "").encode("utf-8")) % len(options)]


# ---------------------------------------------------------------------------
# What kind of broad question is this?  (English, Roman Urdu, Urdu script)
# ---------------------------------------------------------------------------

_MEDICINE_WORDS = r"(medicine|medicines|medication|medications|meds|tablets|pills)"
_TOPIC_PATTERNS: dict[str, list[str]] = {
    "summary": [
        r"\b(discharge|summary|slip|papers?|report)\b.*\b(say|says|said|mention|mentioned|written|contain|contains|cover|covers|about)\b",
        r"\bwhat\b.*\b(mention|mentioned|written|says?)\b.*\b(summary|slip|discharge|papers?)\b",
        r"\bhist[a-z]{2,5}\b",   # history, histroy, histry
        r"\b(my|the) (diagnosis|condition)\b",
        r"\bwhat (happened|do i have|is wrong|was wrong)\b",
        r"\b(summary|slip|discharge)\b.*\b(kya|likha|likhi|mein|main|mn)\b",
        r"\bkya likha\b",
        r"(ڈسچارج|سمری|پرچی|رپورٹ).*(کیا|لکھا)",
    ],
    "medicines": [
        rf"\b(which|what)\b.*\b{_MEDICINE_WORDS}\b",
        rf"\b(any|my|the)\b.*\b{_MEDICINE_WORDS}\b.*\b(continue|take|need|recommended|prescribed|mention|mentioned|slip|list)\b",
        rf"\b(continue|stop)\b.*\b{_MEDICINE_WORDS}\b",
        r"\b(konsi|kaunsi|kon si|kaun si|kya)\b.*\b(dawa|dawai|dawaiyan|goli|goliyan)\b",
        r"\b(dawa|dawai|goli|goliyan)\b.*\b(jaari|jari|continue|leni|khani|lena)\b",
        r"(کون سی|کونسی|کیا).*(دوا|دوائی|دوائیں|گولی|گولیاں)",
        r"(دوا|دوائی|گولی|گولیاں).*(جاری|لینی|لینا|کھانی)",
    ],
    "followup": [
        r"\b(follow\s*-?\s*up|appointment|next visit|check\s*-?\s*up)\b",
        r"\bwhen\b.*\b(come back|see (the )?doctor|visit)\b",
        r"\b(kab|appointment)\b.*\b(aana|aani|jana|dikhana|appointment)\b",
        r"(اپوائنٹمنٹ|فالو اپ|چیک اپ)",
    ],
    "warning_signs": [
        r"\b(warning signs?|danger signs?|red flags?|emergency)\b",
        r"\bwhen (should|do) i (go|call|worry|worried)\b",
        r"\bwhat should i (watch|look) (for|out)\b",
        r"\b(khatra|khatre)\b",
        r"(خطرے|خطرہ|ہنگامی)",
    ],
}

# Order matters: a message asking for "history or any medication" wants the summary.
_TOPIC_ORDER = ["summary", "medicines", "followup", "warning_signs"]


def detect_overview_topic(message: str) -> Optional[str]:
    """
    Broad questions like "what medicines do I take?" cannot be backed by ONE quote
    from the document, so they are answered straight from the care plan. Returns
    summary / medicines / followup / warning_signs, or None for everything else.
    """
    text = (message or "").lower()
    for topic in _TOPIC_ORDER:
        if any(re.search(p, text) for p in _TOPIC_PATTERNS[topic]):
            return topic
    return None


_EMOTION_PATTERNS = [
    r"\b(worried|worry|worrying|anxious|anxiety|scared|afraid|fear|overthink\w*|stress\w*|nervous|panic\w*|sad|depressed|hopeless|crying)\b",
    r"\bwhat if\b",
    r"\bcan'?t stop thinking\b",
    r"\b(ghabrahat|ghabra\w*|pareshan\w*|tension|fikar|fikr|udaas|dar lag\w*|darr)\b",
    r"\b(soch soch|zyada soch\w*)\b",
    r"(پریشان|گھبراہٹ|ڈر|فکر|اداس|ٹینشن)",
]


def is_emotional(message: str) -> bool:
    text = (message or "").lower()
    return any(re.search(p, text) for p in _EMOTION_PATTERNS)


# ---------------------------------------------------------------------------
# Small helpers that read the care plan
# ---------------------------------------------------------------------------

def _medicine_lines(care_plan) -> str:
    meds = list(getattr(care_plan, "medications", None) or [])
    return "\n".join(f"- {m.name}: {m.schedule}" for m in meds)


def _flag_lines(care_plan) -> str:
    flags = list(getattr(care_plan, "red_flags", None) or [])
    return "\n".join(f"- {f}" for f in flags)


_EMERGENCY_LINE = (
    "If you ever feel very unwell, please do not wait. Go to the nearest "
    "emergency department or call emergency services."
)


# ---------------------------------------------------------------------------
# Answers to broad questions (all taken from the care plan, nothing invented)
# ---------------------------------------------------------------------------

def build_medicines_reply(care_plan) -> str:
    lines = _medicine_lines(care_plan)
    if not lines:
        return (
            "I can't see any medicines listed in your discharge summary. "
            "Please check with your care team."
        )
    return (
        "Here are the medicines your discharge summary lists:\n\n"
        f"{lines}\n\n"
        "Please take them the way the summary says. If you are not sure about "
        "one, or a medicine is making you feel unwell, ask your care team "
        "before you change anything."
    )


def build_summary_reply(care_plan) -> str:
    diagnosis = (getattr(care_plan, "diagnosis", "") or "").strip() or "not listed"
    meds = _medicine_lines(care_plan) or "- none listed"
    follow_up = str(getattr(care_plan, "follow_up_date", "") or "").strip() or "no date is listed"
    return (
        "Here is what your discharge summary says.\n\n"
        f"Why you were in hospital: {diagnosis}\n\n"
        f"Your medicines:\n{meds}\n\n"
        f"Your follow-up visit: {follow_up}\n\n"
        "Ask me about any part of this. You can also tell me how you are feeling."
    )


def build_followup_reply(care_plan) -> str:
    follow_up = str(getattr(care_plan, "follow_up_date", "") or "").strip()
    if not follow_up:
        return (
            "I can't see a follow-up date in your discharge summary. "
            "Your care team can tell you when to come back."
        )
    return (
        f"Your discharge summary lists your follow-up visit for {follow_up}. "
        "If you need to change it, please contact your care team."
    )


def build_warning_signs_reply(care_plan) -> str:
    lines = _flag_lines(care_plan)
    intro = (
        "Please contact your care team, or get urgent care, if you notice any of these:\n\n"
        f"{lines}\n\n"
        if lines else ""
    )
    return intro + _EMERGENCY_LINE


def build_overview_reply(topic: str, care_plan) -> str:
    return {
        "summary": build_summary_reply,
        "medicines": build_medicines_reply,
        "followup": build_followup_reply,
        "warning_signs": build_warning_signs_reply,
    }[topic](care_plan)


# ---------------------------------------------------------------------------
# Everything else
# ---------------------------------------------------------------------------

def build_fallback_reply(message: str = "") -> str:
    """The answer is not in the document: say what CAN be done. Never a dead end."""
    opening = _pick(
        [
            "I couldn't find the answer to that in your discharge summary, and I don't want to guess about your health.",
            "That isn't in your discharge summary, so I can't answer it safely.",
        ],
        message,
    )
    return (
        f"{opening}\n\n"
        "Here is what I can help with:\n"
        "- your medicines\n"
        "- your follow-up visit\n"
        "- warning signs to watch for\n"
        "- how you are feeling. Just tell me.\n\n"
        "For anything else, your care team is the best person to ask."
    )


def build_emotional_reply() -> str:
    """For worry and stress. Draft wording: ask a nurse to review and replace it."""
    return (
        "It is very common to feel worried after surgery or a hospital stay. "
        "You are not alone in this.\n\n"
        "It can help to talk to someone you trust. Your care team is also happy "
        "to hear how you are feeling, so please tell them.\n\n"
        "If you ever feel hopeless, or think about hurting yourself, tell someone "
        "you trust right away, or contact emergency services."
    )


def build_mild_symptom_reply(care_plan, message: str = "") -> str:
    """A symptom that did not trigger an alert, or worry. Warm, honest, and clear."""
    if is_emotional(message):
        return build_emotional_reply()
    empathy = _pick(
        ["I'm sorry you're not feeling your best.", "I'm sorry you're feeling this way."],
        message,
    )
    lines = _flag_lines(care_plan)
    watch = (
        "Contact them, or get urgent care, if you notice:\n"
        f"{lines}\n\n"
        if lines else ""
    )
    return (
        f"{empathy} Thank you for telling me.\n\n"
        "I can't judge this from a message, so please keep your care team in the loop.\n\n"
        f"{watch}"
        "If you like, tell me more. For example, tell me your temperature, or how "
        "strong the pain is from 1 to 10. I will check it against your warning signs."
    )


def build_urgent_reply() -> str:
    return (
        "I'm really sorry you're going through this. "
        "I've flagged your message for your care team to review as a priority.\n\n"
        "While you wait, go to the nearest emergency department, or call "
        "emergency services right away, if you have:\n"
        "- trouble breathing\n"
        "- chest pain\n"
        "- bleeding that won't stop\n"
        "- confusion or fainting"
    )


def build_decline_reply(message: str = "") -> str:
    """Off-topic messages. Worry gets a human reply, not a refusal."""
    if is_emotional(message):
        return build_emotional_reply()
    return (
        "I'm here to help with your recovery after leaving hospital. That means "
        "your medicines, your follow-up visit, and warning signs.\n\n"
        "I can't help with other topics, but your care team can. "
        "Is there something about your recovery I can help with?"
    )