"""Tests for everything the assistant says to a patient (graph/replies.py) and for
the places that use it. No AI calls, no database: other modules are faked."""
import importlib.util
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

GRAPH_DIR = Path(__file__).parent / "graph"


def load_replies():
    spec = importlib.util.spec_from_file_location("replies_under_test", GRAPH_DIR / "replies.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = load_replies()

CARE_PLAN = SimpleNamespace(
    diagnosis="Right lower lobe pneumonia",
    medications=[SimpleNamespace(name="Azithromycin", schedule="500mg once daily for 3 days"),
                 SimpleNamespace(name="Prednisone", schedule="20mg every morning")],
    follow_up_date="Nov 20, 2026",
    red_flags=["fever", "severe pain", "difficulty breathing", "chest pain"],
)
NO_DETAILS = SimpleNamespace(diagnosis="", medications=[], follow_up_date="", red_flags=[])

ASKED_BEFORE = [{"content": "I feel tired", "answer": R.build_mild_symptom_reply(CARE_PLAN, "I feel tired")}]

FORBIDDEN = ["don't worry", "do not worry", "nothing to worry", "stop taking", "you can stop",
             "no need to take", "you are fine", "you're fine", "i promise", "guarantee", "diagnos"]


def all_replies():
    return {
        "medicines": R.build_medicines_reply(CARE_PLAN),
        "medicines_none": R.build_medicines_reply(NO_DETAILS),
        "summary": R.build_summary_reply(CARE_PLAN),
        "summary_none": R.build_summary_reply(NO_DETAILS),
        "followup": R.build_followup_reply(CARE_PLAN),
        "followup_none": R.build_followup_reply(NO_DETAILS),
        "warning": R.build_warning_signs_reply(CARE_PLAN),
        "warning_none": R.build_warning_signs_reply(NO_DETAILS),
        "fallback_a": R.build_fallback_reply("a"),
        "fallback_b": R.build_fallback_reply("b"),
        "mild": R.build_mild_symptom_reply(CARE_PLAN, "I feel tired"),
        "mild_none": R.build_mild_symptom_reply(NO_DETAILS, "I feel tired"),
        "mild_ack": R.build_mild_symptom_reply(CARE_PLAN, "pain is 4 out of 10", ASKED_BEFORE),
        "clarify_number": R.build_clarify_number_reply(),
        "emotional": R.build_emotional_reply(),
        "urgent": R.build_urgent_reply(),
        "decline": R.build_decline_reply("what is the weather"),
    }


# ---------------- which broad question is it? ----------------
@pytest.mark.parametrize("message,topic", [
    ("is there any medicine that i need to continue or not?", "medicines"),
    ("which medicines should I take?", "medicines"),
    ("Kaunsi dawai leni hai?", "medicines"),
    ("کیا میں آج اپنی دوا لے سکتا ہوں؟", "medicines"),
    ("what is mention on my discharge summary ??", "summary"),
    ("can i get my breif histroy or any medication recommended in slip?", "summary"),
    ("what does my discharge summary say", "summary"),
    ("What time is my next appointment?", "followup"),
    ("follow up kab hai", "followup"),
    ("what are the warning signs?", "warning_signs"),
])
def test_broad_questions_are_recognised(message, topic):
    assert R.detect_overview_topic(message) == topic


@pytest.mark.parametrize("message", [
    "Can I take painkiller with my antibiotic?",
    "Hey can i eat bolied egg??",
    "Can I drive after taking the antibiotic?",
    "Kya mujhe antibiotics poori khatam karni hain?",
    "Kya antibiotics ke sath dahi kha sakte hain?",
    "کیا مجھے ہسپتال واپس جانا چاہیے اگر بخار آجائے؟",
    "i am feeling tired and little bit pain also what to do now?",
    "I have a fever and high pain",
])
def test_specific_questions_and_symptoms_are_not_hijacked(message):
    assert R.detect_overview_topic(message) is None


@pytest.mark.parametrize("message,expected", [
    ("why am i overthinking my surgery, what if recovery goes wrong?", True),
    ("Thora sa ghabrahat ho rahi hai lekin blood pressure theek hai", True),
    ("mujhe bahut tension ho rahi hai", True),
    ("مجھے بہت ڈر لگ رہا ہے", True),
    ("I have a fever and high pain", False),
    ("Mere seene mein dard hai", False),
    ("can i eat chicken today?", False),
])
def test_worry_is_recognised(message, expected):
    assert R.is_emotional(message) is expected


# ---------------- what the broad answers say ----------------
def test_medicines_reply_lists_every_medicine_and_never_says_to_stop():
    text = R.build_medicines_reply(CARE_PLAN)
    assert "Azithromycin: 500mg once daily for 3 days" in text and "Prednisone: 20mg every morning" in text
    assert "before you change anything" in text


def test_missing_details_are_admitted_not_invented():
    assert "can't see any medicines" in R.build_medicines_reply(NO_DETAILS)
    assert "no date is listed" in R.build_summary_reply(NO_DETAILS)
    assert "can't see a follow-up date" in R.build_followup_reply(NO_DETAILS)


def test_summary_reply_has_every_part():
    text = R.build_summary_reply(CARE_PLAN)
    for part in ("Right lower lobe pneumonia", "Azithromycin", "Nov 20, 2026"):
        assert part in text


def test_warning_signs_reply_lists_the_flags_and_the_emergency_line():
    text = R.build_warning_signs_reply(CARE_PLAN)
    assert all(f"- {flag}" in text for flag in CARE_PLAN.red_flags)
    assert "emergency" in text
    assert "emergency" in R.build_warning_signs_reply(NO_DETAILS)       # still safe with no flags


# ---------------- no more dead ends or cold replies ----------------
def test_fallback_offers_what_the_assistant_can_do():
    for text in (R.build_fallback_reply("a"), R.build_fallback_reply("b")):
        assert "medicines" in text and "follow-up" in text and "warning signs" in text
        assert "care team" in text
        assert "doesn't cover this specific question" not in text


def test_mild_symptom_reply_is_warm_useful_and_honest():
    text = R.build_mild_symptom_reply(CARE_PLAN, "I feel tired and have a little pain")
    assert "sorry" in text.lower() and "Thank you for telling me" in text
    assert all(f"- {flag}" in text for flag in CARE_PLAN.red_flags)
    assert "If anything changes" in text
    assert "from 1 to 10" not in text and "temperature" not in text   # no question we cannot use
    assert "Got it, thanks for the update" not in text
    assert "flagged" not in text                  # we must not claim a nurse was told for mild cases


def test_worry_gets_a_human_reply_everywhere():
    for text in (R.build_mild_symptom_reply(CARE_PLAN, "why am I overthinking, what if it goes wrong"),
                 R.build_decline_reply("I am so worried about my surgery")):
        assert "not alone" in text and "hurting yourself" in text
        assert "- fever" not in text


def test_urgent_reply_says_what_to_do_while_waiting():
    text = R.build_urgent_reply()
    assert "flagged" in text and "emergency" in text
    for sign in ("trouble breathing", "chest pain", "bleeding", "confusion"):
        assert sign in text


def test_decline_invites_the_patient_back_in():
    text = R.build_decline_reply("what is the weather")
    assert "recovery" in text and text.rstrip().endswith("?")
    assert "I can only help" not in text


# ---------------- safety and readability of EVERY reply ----------------
@pytest.mark.parametrize("name", list(all_replies()))
def test_no_risky_phrases_and_no_overpromising(name):
    text = all_replies()[name].lower()
    for bad in FORBIDDEN:
        assert bad not in text, (name, bad)
    assert "within" not in text and "minutes" not in text      # no promised response times


def _syllables(word):
    word = re.sub(r"[^a-z]", "", word.lower())
    if not word:
        return 0
    count = len(re.findall(r"[aeiouy]+", word))
    if word.endswith("e") and not word.endswith(("le", "ee")) and count > 1:
        count -= 1
    return max(1, count)


def _reading_ease(text):
    sentences = [s for s in re.split(r"[.!?\n]+", text) if s.strip()]
    words = re.findall(r"[A-Za-z']+", text)
    syllables = sum(_syllables(w) for w in words)
    return (206.835 - 1.015 * len(words) / len(sentences) - 84.6 * syllables / len(words),
            len(words) / len(sentences))


@pytest.mark.parametrize("name", ["fallback_a", "fallback_b", "mild", "mild_none", "mild_ack", "clarify_number", "emotional",
                                  "urgent", "decline", "followup_none", "warning"])
def test_fixed_replies_are_plain_language(name):
    """Health-literacy guidance: short sentences, easy words (Flesch reading ease 65+)."""
    score, avg_sentence = _reading_ease(all_replies()[name])
    assert score >= 65, f"{name}: reading ease {score:.0f}"
    assert avg_sentence <= 18, f"{name}: {avg_sentence:.0f} words per sentence"


@pytest.mark.parametrize("name", list(all_replies()))
def test_replies_fit_comfortably_in_one_whatsapp_message(name):
    assert len(all_replies()[name]) < 900


# ---------------- the places that use the replies (other modules faked) ----------------
@pytest.fixture
def modules(monkeypatch):
    calls = SimpleNamespace(llm=0, logged=[], alerts=[], guidance=None, llm_result=None)

    graph = types.ModuleType("graph"); graph.__path__ = [str(GRAPH_DIR)]
    provider = types.ModuleType("graph.llm_provider")

    def call_structured(**kwargs):
        calls.llm += 1
        return calls.llm_result

    provider.call_structured = call_structured
    schemas = types.ModuleType("graph.schemas"); schemas.CarePlan = object
    config = types.ModuleType("config"); config.require_api_key = lambda: None
    guidance = types.ModuleType("general_guidance")
    guidance.find_general_guidance = lambda message: calls.guidance
    storage = types.ModuleType("storage")
    storage.log_message = lambda **k: calls.logged.append(k)
    pushover = types.ModuleType("pushover_notify")
    pushover.send_pushover_notification = lambda title, message: calls.alerts.append((title, message))

    for name, mod in {"graph": graph, "graph.llm_provider": provider, "graph.schemas": schemas,
                      "config": config, "general_guidance": guidance, "storage": storage,
                      "pushover_notify": pushover}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    for name in ("graph.replies", "graph.answering", "graph.nodes"):
        monkeypatch.delitem(sys.modules, name, raising=False)

    import importlib
    calls.answering = importlib.import_module("graph.answering")
    calls.nodes = importlib.import_module("graph.nodes")
    return calls


def ask(modules, message, doc="Resume a regular diet with no activity restrictions."):
    return modules.answering.answer_question_real(message, CARE_PLAN, doc, "en")


def test_broad_question_is_answered_from_the_care_plan_without_any_ai_call(modules):
    result = ask(modules, "is there any medicine that i need to continue or not?")
    assert result["source"] == "care_plan" and result["grounded"] is True
    assert "Azithromycin" in result["answer"] and modules.llm == 0


def test_specific_question_still_uses_the_verified_document_quote(modules):
    modules.llm_result = {"answer": "Yes, a regular diet is fine.", "grounded": True,
                          "source_quote": "Resume a regular diet with no activity restrictions"}
    result = ask(modules, "can i eat boiled eggs?")
    assert result["source"] == "document" and modules.llm == 1


def test_a_made_up_quote_is_still_rejected(modules):
    modules.llm_result = {"answer": "Yes, definitely.", "grounded": True,
                          "source_quote": "eggs are always completely safe for everyone"}
    result = ask(modules, "can i eat boiled eggs?")
    assert result["source"] == "soft_reply" and result["grounded"] is False


def test_general_guidance_layer_is_unchanged(modules):
    modules.llm_result = {"answer": "x", "grounded": False, "source_quote": ""}
    modules.guidance = "Gentle walking is usually fine."
    result = ask(modules, "can i walk outside?")
    assert result["source"] == "general_guidance" and result["answer"] == "Gentle walking is usually fine."


def test_unanswerable_question_gets_the_friendly_fallback(modules):
    modules.llm_result = {"answer": "x", "grounded": False, "source_quote": ""}
    result = ask(modules, "what is the capital of mars?")
    assert result["source"] == "soft_reply"
    assert "warning signs" in result["answer"] and "doesn't cover this specific question" not in result["answer"]


def test_low_risk_symptom_gets_a_real_reply_and_it_is_logged(modules):
    state = {"patient_id": "p1", "patient_message": "i am feeling tired and little bit pain, what to do?",
             "care_plan": CARE_PLAN, "intent": "symptom_report", "risk_level": "low", "triage_reasoning": "mild"}
    result = modules.nodes.log_normal(state)
    assert result["escalated"] is False and "Thank you for telling me" in result["answer"]
    assert modules.logged[0]["answer"] == result["answer"]             # dashboard shows what the patient saw


def test_urgent_case_gets_safety_advice_and_a_minimal_alert(modules):
    state = {"patient_id": "+923001234567", "patient_message": "I have a fever and high pain",
             "care_plan": CARE_PLAN, "intent": "symptom_report", "risk_level": "high", "triage_reasoning": "fever"}
    result = modules.nodes.escalate(state)
    assert result["escalated"] is True and "emergency" in result["answer"]
    assert modules.logged[0]["answer"] == result["answer"] and modules.logged[0]["escalated"] is True
    assert modules.logged[0]["priority"] == "urgent"                  # so the dashboard files it as urgent
    title, message = modules.alerts[0]
    assert "fever" not in (title + message).lower() and "923001234567" not in title


def test_off_topic_and_worry_nodes(modules):
    base = {"patient_id": "p1", "intent": "off_topic", "care_plan": CARE_PLAN}
    plain = modules.nodes.decline_off_topic({**base, "patient_message": "what is the weather?"})
    worried = modules.nodes.decline_off_topic({**base, "patient_message": "why am i overthinking my surgery"})
    assert "recovery" in plain["answer"] and "not alone" in worried["answer"]
    assert plain["escalated"] is False and worried["escalated"] is False


# ---------------- blank doses, repeated replies, bare numbers ----------------
BLANK_DOSE_PLAN = SimpleNamespace(
    diagnosis="x", follow_up_date="", red_flags=["fever"],
    medications=[SimpleNamespace(name="ceftriaxone", schedule=", "),
                 SimpleNamespace(name="Zithromycin", schedule=", daily until gone"),
                 SimpleNamespace(name="steroids", schedule=""),
                 SimpleNamespace(name="Azithromycin", schedule="500mg, once daily")],
)


def test_medicines_without_doses_show_just_the_name():
    text = R.build_medicines_reply(BLANK_DOSE_PLAN)
    assert "- ceftriaxone\n" in text and "- steroids\n" in text
    assert "- Zithromycin: daily until gone" in text
    assert "- Azithromycin: 500mg, once daily" in text           # inner commas are kept
    assert ": ," not in text and ": \n" not in text


def test_summary_also_hides_empty_doses():
    text = R.build_summary_reply(BLANK_DOSE_PLAN)
    assert "- ceftriaxone" in text and ": ," not in text


def test_a_second_reply_is_a_short_thank_you_not_a_repeat():
    first = R.build_mild_symptom_reply(CARE_PLAN, "I feel tired")
    second = R.build_mild_symptom_reply(CARE_PLAN, "my pain is 4 out of 10",
                                        [{"content": "I feel tired", "answer": first}])
    assert "Thank you, that helps" in second
    assert "- fever" not in second and len(second) < len(first) * 0.7
    third = R.build_mild_symptom_reply(CARE_PLAN, "still the same",
                                       [{"content": "x", "answer": first}, {"content": "y", "answer": second}])
    assert "Thank you, that helps" in third                      # stays short, does not flip back


def test_the_full_reply_returns_after_an_unrelated_exchange():
    history = [{"content": "tired", "answer": R.build_mild_symptom_reply(CARE_PLAN, "tired")},
               {"content": "my medicines?", "answer": R.build_medicines_reply(CARE_PLAN)}]
    assert "- fever" in R.build_mild_symptom_reply(CARE_PLAN, "I feel dizzy", history)


@pytest.mark.parametrize("message", ["4", " 4 ", "4/10", "7 / 10", "4 out of 10", "10.", "٤", "۴"])
def test_bare_numbers_are_recognised(message):
    assert R.is_bare_number(message)


@pytest.mark.parametrize("message", ["4 hours ago", "I take 2 tablets", "pain 4", "my pain is 4 out of 10",
                                     "", "1234567", "fever"])
def test_numbers_with_meaning_are_not_bare(message):
    assert not R.is_bare_number(message)


def test_a_bare_number_gets_a_gentle_question_everywhere(modules):
    for call in (lambda: R.build_decline_reply("4"), lambda: R.build_mild_symptom_reply(CARE_PLAN, "4"),
                 lambda: ask(modules, "4")["answer"]):
        text = call()
        assert "not sure what that number is about" in text and "my pain is 4 out of 10" in text
    assert modules.llm == 0                                       # no AI call, no "couldn't find it in your summary"
    assert ask(modules, "4")["source"] == "clarify"


def test_the_node_uses_the_conversation_to_avoid_repeating(modules):
    first = R.build_mild_symptom_reply(CARE_PLAN, "I feel tired")
    state = {"patient_id": "p1", "patient_message": "my pain is 4 out of 10", "care_plan": CARE_PLAN,
             "intent": "symptom_report", "risk_level": "low", "triage_reasoning": "mild",
             "conversation_history": [{"content": "I feel tired", "answer": first}]}
    result = modules.nodes.log_normal(state)
    assert "Thank you, that helps" in result["answer"] and modules.logged[0]["answer"] == result["answer"]