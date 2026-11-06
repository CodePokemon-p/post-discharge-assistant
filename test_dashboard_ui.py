"""Tests for the nurse dashboard and the home page (dashboard_ui.py)."""
import re
from datetime import datetime, timedelta, timezone

import pytest

import dashboard_ui as ui

NOW = datetime.now(timezone.utc).replace(tzinfo=None)
XSS = "<script>alert(1)</script>"


def row(**kw):
    base = {"id": 1, "patient_id": "+923001234567", "content": "I have a fever", "intent": "symptom_report",
            "risk_level": "high", "answer": "We flagged it", "escalated": 1, "priority": "urgent",
            "reasoning": "Matched a red flag", "timestamp": NOW, "resolved": False}
    base.update(kw)
    return base


def counts(page):
    urgent = int(re.search(r"Urgent \((\d+)\)", page).group(1))
    queue = int(re.search(r"Needs a callback or a look \((\d+)\)", page).group(1))
    return urgent, queue


# ---------------- urgent vs not ----------------
def test_high_risk_cases_are_urgent_even_when_older_rows_have_no_priority():
    """The bug seen live: HIGH RISK messages were listed as Low Priority."""
    rows = [row(id=1, priority=None, risk_level="high"),          # saved before priority existed
            row(id=2, priority="urgent", risk_level=None),
            row(id=3, priority="low", risk_level=None),
            row(id=4, priority=None, risk_level=None),
            row(id=5, priority="urgent", resolved=True)]          # resolved: hidden
    assert counts(ui.render_dashboard(rows, [])) == (2, 2)


def test_resolved_cases_never_appear():
    page = ui.render_dashboard([row(id=9, resolved=True, content="SECRET-RESOLVED")], [])
    assert "SECRET-RESOLVED" not in page and counts(page) == (0, 0)


def test_empty_states_are_friendly():
    page = ui.render_dashboard([], [])
    assert "No urgent cases right now." in page and "Nothing waiting." in page and "No messages yet." in page


# ---------------- safety ----------------
def test_everything_from_patients_and_the_ai_is_escaped():
    evil = row(patient_id=XSS, content=XSS, reasoning=XSS, answer=XSS, intent=XSS, risk_level=XSS)
    page = ui.render_dashboard([evil], [evil])
    assert XSS not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_a_quote_in_a_message_cannot_break_out_of_an_attribute():
    page = ui.render_dashboard([row(patient_id='+92300"><img src=x onerror=alert(1)>')], [])
    assert "<img" not in page


def test_no_external_scripts_fonts_or_images_are_loaded():
    for page in (ui.render_dashboard([row()], [row()]), ui.render_home("https://github.com/x/y")):
        assert "<script" not in page and "src=" not in page
        assert "@import" not in page and "url(" not in page


# ---------------- buttons ----------------
def test_call_link_keeps_the_plus_sign():
    page = ui.render_dashboard([row(patient_id="+923001234567")], [])
    assert 'href="tel:+923001234567"' in page


def test_resolve_is_a_post_form_and_urgent_cases_ask_for_confirmation():
    page = ui.render_dashboard([row(id=7), row(id=8, priority="low", risk_level=None)], [])
    assert 'method="post" action="/dashboard/resolve/7"' in page and 'action="/dashboard/resolve/8"' in page
    urgent_form = re.search(r'<form[^>]*resolve/7"[^>]*>', page).group(0)
    queue_form = re.search(r'<form[^>]*resolve/8"[^>]*>', page).group(0)
    assert "confirm(" in urgent_form and "confirm(" not in queue_form


def test_a_row_without_an_id_has_no_resolve_button():
    assert "/dashboard/resolve/" not in ui.render_dashboard([row(id=None)], [])


def test_privacy_mode_hides_numbers_and_the_call_link_everywhere():
    page = ui.render_dashboard([row()], [row()], mask=True)
    assert "923001234567" not in page and "tel:" not in page
    assert "***4567" in page and "Show phone numbers" in page
    assert "923001234567" in ui.render_dashboard([row()], [row()], mask=False)


# ---------------- look and behaviour ----------------
def test_dark_theme_and_refresh_options():
    page = ui.render_dashboard([], [])
    assert '<meta name="color-scheme" content="dark">' in page and "color-scheme:dark" in page
    assert 'http-equiv="refresh"' in page
    assert 'http-equiv="refresh"' not in ui.render_dashboard([], [], auto_refresh=False)


@pytest.mark.parametrize("delta,label", [(timedelta(seconds=10), "just now"), (timedelta(minutes=5), "5 min ago"),
                                         (timedelta(hours=2), "2 h ago"), (timedelta(days=3), "3 d ago")])
def test_times_are_shown_in_plain_words(delta, label):
    assert label in ui.render_dashboard([row(timestamp=NOW - delta)], [])


def test_timezone_aware_timestamps_and_missing_ones_do_not_crash():
    ui.render_dashboard([row(timestamp=datetime.now(timezone.utc)), row(id=2, timestamp=None)], [])


def test_long_messages_are_shortened_in_the_recent_table_only():
    long_text = "word " * 100
    page = ui.render_dashboard([], [row(content=long_text)])
    assert "..." in page and long_text.strip() not in page


def test_the_patient_is_shown_what_was_said_to_them():
    page = ui.render_dashboard([row(answer="Please go to the emergency department")], [])
    assert "What the patient was told" in page and "Please go to the emergency department" in page


# ---------------- home page ----------------
def test_home_page_content_and_links():
    page = ui.render_home("https://github.com/someone/post-discharge-assistant")
    assert "Post-Discharge Assistant" in page and "not medical advice" in page
    assert 'href="/dashboard"' in page and 'href="/health"' in page
    assert 'href="https://github.com/someone/post-discharge-assistant"' in page
    assert '<meta name="color-scheme" content="dark">' in page


@pytest.mark.parametrize("typed", ['"https://github.com/a/b"', "  https://github.com/a/b  ", "'https://github.com/a/b'",
                                   "github.com/a/b", "HTTPS://github.com/a/b"])
def test_home_page_forgives_common_typing_mistakes_in_the_source_link(typed):
    """The link vanished live because of how it was typed into Render's settings box."""
    page = ui.render_home(typed)
    assert "Source code and demo video" in page
    assert 'href="https://github.com/a/b"' in page or 'href="HTTPS://github.com/a/b"' in page


@pytest.mark.parametrize("url", [None, "", "   ", "javascript:alert(1)", "not a link", "ftp://example.com/x", "data:text/html,hi"])
def test_home_page_ignores_a_missing_or_unsafe_source_link(url):
    page = ui.render_home(url)
    assert "Source code and demo video" not in page and "javascript:" not in page


def test_home_page_escapes_the_source_link():
    page = ui.render_home('https://example.com/"><script>alert(1)</script>')
    assert "<script>" not in page