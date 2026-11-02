"""Runs your real webhook.py with graph/storage/messaging/pushover replaced by
fakes, so no database, LLM or WhatsApp account is needed."""
import asyncio
import base64
import hashlib
import hmac
import importlib
import itertools
import json
import logging
import sys
import types
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

APP_SECRET = "test-app-secret"
VERIFY_TOKEN = "test-verify-token"
PHONE_DIGITS = "923001234567"
PHONE = "+" + PHONE_DIGITS
OTHER_DIGITS = "923009999999"
SECRET_WORD = "zebrafishsymptom"
DIAGNOSIS = "DX-APPENDECTOMY-XYZ"
ADMIN = ("nurse", "s3cret!")
XSS = "<script>alert(1)</script>"
_ids = itertools.count(1)


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()


def b64_of(digits: str) -> str:
    return base64.b64encode(digits.encode()).decode()


def wa_payload(text, number=PHONE_DIGITS, msg_id=None):
    # Like a real wamid: the sender's number is hidden inside it, base64-encoded.
    msg_id = msg_id or f"wamid.HBgM{b64_of(number)}FQIA{next(_ids)}"
    return {"entry": [{"changes": [{"value": {"messages": [
        {"from": number, "id": msg_id, "type": "text", "text": {"body": text}}
    ]}}]}]}


def wa_non_text(kind, number=PHONE_DIGITS):
    """A voice note, photo, sticker... : has a type but no text body."""
    msg_id = f"wamid.HBgM{b64_of(number)}FQIA{next(_ids)}"
    return {"entry": [{"changes": [{"value": {"messages": [
        {"from": number, "id": msg_id, "type": kind, kind: {"id": "media123"}}
    ]}}]}]}


def post_signed(client, payload):
    raw = json.dumps(payload).encode()
    return client.post("/meta/webhook", content=raw, headers={"X-Hub-Signature-256": sign(raw)})


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", ADMIN[0])
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN[1])
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("META_VERIFY_TOKEN", VERIFY_TOKEN)
    monkeypatch.setenv("META_WHATSAPP_TOKEN", "x")
    monkeypatch.setenv("META_PHONE_NUMBER_ID", "1")


@pytest.fixture
def ctx(monkeypatch, env):
    """Everything the tests need: the client plus recorders for each fake."""
    c = SimpleNamespace(
        sent=[], alerts=[], logged=[], claimed=set(), graph_calls=[],
        graph_error=None, send_error=None, events=[], graph_result=None,
        stored_text={}, saved=[],
    )
    plan = SimpleNamespace(
        diagnosis=DIAGNOSIS,
        medications=[SimpleNamespace(name="drugA", schedule="daily")],
        red_flags=["fever"],
        follow_up_date=None,
    )

    def invoke(state):
        c.events.append("graph-started")
        c.graph_calls.append(state)
        if c.graph_error:
            raise c.graph_error
        return c.graph_result or {"answer": "ANSWER-TEXT-PRIVATE", "escalated": False}

    def send(to, text):
        if c.send_error:
            raise c.send_error
        c.sent.append((to, text))

    def claim(message_id):
        if message_id in c.claimed:
            return False
        c.claimed.add(message_id)
        return True

    fake_graph = types.ModuleType("graph"); fake_graph.__path__ = []
    fake_build = types.ModuleType("graph.build")
    fake_build.build_graph = lambda: SimpleNamespace(invoke=invoke)
    fake_extraction = types.ModuleType("graph.extraction")
    fake_extraction.extract_care_plan_real = lambda text: plan
    fake_extraction.extract_text_from_image = lambda b64, mime: "text from image"

    fake_storage = types.ModuleType("storage")
    fake_storage.init_db = lambda: None
    fake_storage.get_care_plan = lambda p: plan if p == PHONE else None
    fake_storage.save_care_plan = lambda *a, **k: c.saved.append((a, k))
    fake_storage.get_discharge_text = lambda p: c.stored_text.get(p)
    fake_storage.claim_message = claim
    fake_storage.log_message = lambda **k: c.logged.append(k)
    fake_storage.resolve_message = lambda message_id: None
    fake_storage.get_escalations = lambda: [
        {"patient_id": PHONE, "priority": "urgent", "risk_level": "high",
         "content": XSS, "reasoning": "<b>bold</b>"}
    ]
    fake_storage.get_all_messages = lambda: [
        {"patient_id": PHONE, "intent": "question", "escalated": False,
         "content": XSS, "answer": "ok"}
    ]
    fake_messaging = types.ModuleType("messaging")
    fake_messaging.send_whatsapp_message = send
    fake_pushover = types.ModuleType("pushover_notify")
    fake_pushover.send_pushover_notification = lambda title, message: c.alerts.append((title, message))

    for name, mod in {
        "graph": fake_graph, "graph.build": fake_build, "graph.extraction": fake_extraction,
        "storage": fake_storage, "messaging": fake_messaging, "pushover_notify": fake_pushover,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, "webhook", raising=False)

    webhook = importlib.import_module("webhook")
    c.client = TestClient(webhook.app)
    return c


# ---------------- Meta GET handshake ----------------
def test_handshake_ok(ctx):
    r = ctx.client.get("/meta/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "12345"})
    assert r.status_code == 200 and r.text == "12345"


def test_handshake_wrong_token(ctx):
    r = ctx.client.get("/meta/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"})
    assert r.status_code == 403


def test_handshake_fails_when_token_not_configured(ctx, monkeypatch):
    monkeypatch.delenv("META_VERIFY_TOKEN")
    r = ctx.client.get("/meta/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"})
    assert r.status_code == 403


# ---------------- Signature ----------------
def test_unsigned_post_rejected_and_nothing_happens(ctx):
    r = ctx.client.post("/meta/webhook", json=wa_payload("hello"))
    assert r.status_code == 403 and ctx.sent == [] and ctx.claimed == set()


def test_wrong_signature_rejected(ctx):
    raw = json.dumps(wa_payload("hello")).encode()
    r = ctx.client.post("/meta/webhook", content=raw, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64})
    assert r.status_code == 403 and ctx.sent == []


def test_valid_signature_is_accepted_then_processed_in_background(ctx):
    r = post_signed(ctx.client, wa_payload("hello"))
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert ctx.sent == [(PHONE, "ANSWER-TEXT-PRIVATE")]   # background work finished


def test_valid_signature_but_not_json_is_400(ctx):
    raw = b"not json"
    r = ctx.client.post("/meta/webhook", content=raw, headers={"X-Hub-Signature-256": sign(raw)})
    assert r.status_code == 400


def test_status_updates_are_ignored(ctx):
    r = post_signed(ctx.client, {"entry": [{"changes": [{"value": {"statuses": [{}]}}]}]})
    assert r.status_code == 200 and r.json()["status"] == "ignored" and ctx.sent == []


def test_message_without_id_is_ignored(ctx):
    payload = wa_payload("hello")
    del payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"]
    r = post_signed(ctx.client, payload)
    assert r.json()["status"] == "ignored" and ctx.sent == []


def test_unregistered_patient_gets_polite_decline(ctx):
    r = post_signed(ctx.client, wa_payload("hi", number=OTHER_DIGITS))
    assert r.json()["status"] == "accepted"
    assert "don't have your discharge information" in ctx.sent[0][1]
    assert ctx.graph_calls == []


# ---------------- Full document reaches the answering agent ----------------
FULL_DOC = "Diagnosis: pneumonia. Precautions: avoid smoking and cold drinks. Diet: regular."


def test_answer_agent_gets_the_full_stored_document(ctx):
    ctx.stored_text[PHONE] = FULL_DOC
    post_signed(ctx.client, wa_payload("any precautions?"))
    assert ctx.graph_calls[0]["discharge_text"] == FULL_DOC


def test_older_patients_fall_back_to_the_rebuilt_summary(ctx):
    post_signed(ctx.client, wa_payload("any precautions?"))          # nothing stored for this patient
    text = ctx.graph_calls[0]["discharge_text"]
    assert text.startswith("DISCHARGE SUMMARY") and DIAGNOSIS in text


def test_intake_keeps_the_whole_document(ctx):
    ctx.client.post("/intake", auth=ADMIN, data={"phone_number": PHONE},
                    files={"file": ("d.txt", FULL_DOC.encode(), "text/plain")},
                    follow_redirects=False)
    args, kwargs = ctx.saved[0]
    assert kwargs["discharge_text"] == FULL_DOC


def test_document_text_is_never_logged(ctx, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    ctx.stored_text[PHONE] = FULL_DOC
    post_signed(ctx.client, wa_payload("any precautions?"))
    ctx.client.post("/intake", auth=ADMIN, data={"phone_number": PHONE},
                    files={"file": ("d.txt", FULL_DOC.encode(), "text/plain")}, follow_redirects=False)
    logs = caplog.text + capsys.readouterr().out
    assert "avoid smoking" not in logs and "full stored" in logs


# ---------------- Phase B: idempotency ----------------
def test_duplicate_message_is_processed_only_once(ctx):
    payload = wa_payload("hello", msg_id="wamid.SAME")
    first = post_signed(ctx.client, payload)
    second = post_signed(ctx.client, payload)
    third = post_signed(ctx.client, payload)
    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate" and third.json()["status"] == "duplicate"
    assert len(ctx.graph_calls) == 1 and len(ctx.sent) == 1


def test_different_messages_are_each_processed(ctx):
    post_signed(ctx.client, wa_payload("one"))
    post_signed(ctx.client, wa_payload("two"))
    assert len(ctx.graph_calls) == 2 and len(ctx.sent) == 2


def test_meta_gets_its_reply_before_the_graph_runs(ctx):
    """The whole point of background processing: answer Meta first, work second."""
    raw = json.dumps(wa_payload("hello")).encode()
    app = sys.modules["webhook"].app

    async def run():
        sent_body = False

        async def receive():
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": raw, "more_body": False}
            await asyncio.sleep(3600)

        async def send(msg):
            if msg["type"] == "http.response.body" and not msg.get("more_body"):
                ctx.events.append("meta-answered")

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "path": "/meta/webhook", "raw_path": b"/meta/webhook",
            "query_string": b"", "scheme": "http", "server": ("test", 80), "client": ("test", 1),
            "headers": [
                (b"x-hub-signature-256", sign(raw).encode()),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(raw)).encode()),
            ],
        }
        await app(scope, receive, send)

    asyncio.run(run())
    assert ctx.events == ["meta-answered", "graph-started"], ctx.events


def test_database_down_means_500_so_meta_retries(ctx, monkeypatch):
    def boom(_):
        raise RuntimeError("db down")
    monkeypatch.setattr(sys.modules["storage"], "claim_message", boom)
    # The webhook module imported the name directly, so patch it there too.
    monkeypatch.setattr(sys.modules["webhook"], "claim_message", boom)
    client = TestClient(sys.modules["webhook"].app, raise_server_exceptions=False)
    raw = json.dumps(wa_payload("hello")).encode()
    r = client.post("/meta/webhook", content=raw, headers={"X-Hub-Signature-256": sign(raw)})
    assert r.status_code == 500 and ctx.sent == []


# ---------------- Phase B: fail-safe when background work breaks ----------------
def test_graph_failure_is_not_silent(ctx, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    ctx.graph_error = ValueError(f"model returned bad data containing {SECRET_WORD}")
    r = post_signed(ctx.client, wa_payload(f"my {SECRET_WORD} is bad"))
    assert r.status_code == 200                                   # Meta still got its 200
    # 1. recorded on the dashboard as urgent, with the message so a human can read it
    assert len(ctx.logged) == 1
    rec = ctx.logged[0]
    assert rec["escalated"] is True and rec["priority"] == "urgent"
    assert SECRET_WORD in rec["content"] and rec["patient_id"] == PHONE
    # 2. nurse alerted, with no patient content in the alert
    assert len(ctx.alerts) == 1
    title, msg = ctx.alerts[0]
    assert "***4567" in title
    for bad in (PHONE_DIGITS, SECRET_WORD):
        assert bad not in title and bad not in msg
    # 3. patient got the safe fallback
    assert ctx.sent == [(PHONE, sys.modules["webhook"].FALLBACK_REPLY)]
    # logs: error type + location, never content
    logs = caplog.text + capsys.readouterr().out
    assert "processing failed" in logs and "type=ValueError" in logs and "at=" in logs
    for bad in (PHONE_DIGITS, SECRET_WORD, "ANSWER-TEXT-PRIVATE"):
        assert bad not in logs


class FakeHttpError(Exception):
    """Looks like requests.HTTPError: carries a .response with status and JSON body."""
    def __init__(self, status, code, message="Session has expired for +923001234567"):
        super().__init__(f"{status} error: {message}")
        self.response = SimpleNamespace(
            status_code=status, json=lambda: {"error": {"code": code, "message": message}})


def test_send_failure_after_a_normal_answer_is_flagged_low_priority(ctx):
    ctx.send_error = ConnectionError("network down")
    r = post_signed(ctx.client, wa_payload("hello"))
    assert r.status_code == 200
    assert ctx.alerts == []                                       # no pager for a normal question
    assert len(ctx.logged) == 1
    assert ctx.logged[0]["priority"] == "low" and ctx.logged[0]["escalated"] is True
    assert "could not be delivered" in ctx.logged[0]["reasoning"]


def test_send_failure_after_an_urgent_case_does_not_alert_twice(ctx, caplog):
    """Seen in real logs: the nurse was alerted by triage, then alerted AGAIN when the send failed."""
    caplog.set_level(logging.INFO)
    ctx.graph_result = {"answer": None, "escalated": True}
    ctx.send_error = FakeHttpError(401, 190)
    r = post_signed(ctx.client, wa_payload("I have chest pain"))
    assert r.status_code == 200
    assert ctx.alerts == [] and ctx.logged == []                  # no duplicate record, no duplicate alert
    assert "nurse was already alerted" in caplog.text


def test_http_failures_log_status_and_meta_code_but_never_the_message_text(ctx, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    ctx.send_error = FakeHttpError(401, 190)
    post_signed(ctx.client, wa_payload("hello"))
    logs = caplog.text + capsys.readouterr().out
    assert "http_status=401" in logs and "meta_error_code=190" in logs
    assert "Session has expired" not in logs and PHONE_DIGITS not in logs


# ---------------- Logging: no PHI ----------------
def test_logs_and_stdout_contain_no_phi(ctx, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    post_signed(ctx.client, wa_payload(f"my {SECRET_WORD} is bad"))
    post_signed(ctx.client, wa_payload("hi", number=OTHER_DIGITS))
    logs = caplog.text + capsys.readouterr().out
    assert "patient=" in logs                      # we do log events...
    for forbidden in (PHONE_DIGITS, OTHER_DIGITS, SECRET_WORD, "ANSWER-TEXT-PRIVATE", DIAGNOSIS):
        assert forbidden not in logs               # ...but never PHI


def test_duplicate_log_line_has_no_content_and_no_raw_message_id(ctx, caplog):
    caplog.set_level(logging.INFO)
    payload = wa_payload(f"my {SECRET_WORD}", msg_id="wamid.DUP1")
    post_signed(ctx.client, payload)
    post_signed(ctx.client, payload)
    assert "duplicate message skipped msg=" in caplog.text
    assert "wamid.DUP1" not in caplog.text and SECRET_WORD not in caplog.text


def test_message_id_never_appears_in_logs_because_it_hides_the_phone_number(ctx, caplog, capsys):
    """Found in real logs: wamid.XXXX contains the sender's number in base64."""
    caplog.set_level(logging.DEBUG)
    ctx.graph_error = None
    post_signed(ctx.client, wa_payload("hello"))                      # normal path
    ctx.graph_error = ValueError("boom")
    post_signed(ctx.client, wa_payload("hello again"))                # failure path
    logs = caplog.text + capsys.readouterr().out
    assert "wamid" not in logs
    assert b64_of(PHONE_DIGITS) not in logs


def test_timing_lines_are_logged_without_content(ctx, caplog):
    caplog.set_level(logging.INFO)
    post_signed(ctx.client, wa_payload(f"my {SECRET_WORD}"))
    for expected in ("care plan lookup took", "graph took", "whatsapp send took", "handled in"):
        assert expected in caplog.text, expected
    assert SECRET_WORD not in caplog.text


def test_dashboard_refreshes_itself(ctx):
    assert 'http-equiv="refresh"' in ctx.client.get("/dashboard", auth=ADMIN).text


def test_intake_logs_contain_no_diagnosis_or_number(ctx, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    r = ctx.client.post("/intake", auth=ADMIN, data={"phone_number": PHONE},
                        files={"file": ("d.txt", b"discharge text", "text/plain")},
                        follow_redirects=False)
    assert r.status_code == 303
    logs = caplog.text + capsys.readouterr().out
    assert DIAGNOSIS not in logs and PHONE_DIGITS not in logs


# ---------------- Admin auth ----------------
@pytest.mark.parametrize("path", ["/dashboard", "/intake"])
def test_admin_pages_need_login(ctx, path):
    assert ctx.client.get(path).status_code == 401
    assert ctx.client.get(path, auth=ADMIN).status_code == 200


def test_intake_post_needs_login(ctx):
    r = ctx.client.post("/intake", data={"phone_number": PHONE},
                        files={"file": ("d.txt", b"x", "text/plain")}, follow_redirects=False)
    assert r.status_code == 401


def test_health_is_public(ctx):
    assert ctx.client.get("/health").json() == {"status": "ok"}


# ---------------- XSS ----------------
def test_dashboard_escapes_patient_text(ctx):
    page = ctx.client.get("/dashboard", auth=ADMIN).text
    assert XSS not in page and "<b>bold</b>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_intake_form_has_no_real_looking_number(ctx):
    assert "3166568948" not in ctx.client.get("/intake", auth=ADMIN).text


# ---------------- Non-text WhatsApp messages ----------------
@pytest.mark.parametrize("kind", ["audio", "image", "video", "document", "location", "unsupported"])
def test_unreadable_messages_get_a_safe_reply_and_a_low_priority_note(ctx, kind):
    r = post_signed(ctx.client, wa_non_text(kind))
    assert r.json()["status"] == "accepted"
    assert ctx.sent == [(PHONE, sys.modules["webhook"].UNSUPPORTED_REPLY)]
    assert ctx.graph_calls == []                      # never run the graph on an empty message
    assert ctx.alerts == []                           # no pager: not proven urgent
    assert len(ctx.logged) == 1
    rec = ctx.logged[0]
    assert rec["priority"] == "low" and rec["escalated"] is True and rec["intent"] == "unsupported_type"
    assert kind in rec["content"]


def test_unreadable_message_from_unregistered_number_gets_the_registration_decline(ctx):
    post_signed(ctx.client, wa_non_text("audio", number=OTHER_DIGITS))
    assert "don't have your discharge information" in ctx.sent[0][1]
    assert ctx.logged == [] and ctx.graph_calls == []


@pytest.mark.parametrize("kind", ["reaction", "sticker", "system"])
def test_reactions_and_stickers_need_no_reply(ctx, kind):
    r = post_signed(ctx.client, wa_non_text(kind))
    assert r.status_code == 200
    assert ctx.sent == [] and ctx.graph_calls == [] and ctx.logged == [] and ctx.alerts == []


def test_unreadable_message_logs_hold_no_phone_number(ctx, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    post_signed(ctx.client, wa_non_text("audio"))
    logs = caplog.text + capsys.readouterr().out
    assert "unsupported message type=audio" in logs
    assert PHONE_DIGITS not in logs and b64_of(PHONE_DIGITS) not in logs


# ---------------- Intake validation ----------------
def intake(ctx, phone, name="d.txt", content=b"discharge text", mime="text/plain"):
    return ctx.client.post("/intake", auth=ADMIN, data={"phone_number": phone},
                           files={"file": (name, content, mime)}, follow_redirects=False)


@pytest.mark.parametrize("bad", ["03001234567", "923001234567", "+92", "+92abc3001234", "not a number", "+9230012345678901234"])
def test_intake_rejects_phone_numbers_that_would_never_match(ctx, bad):
    r = intake(ctx, bad)
    assert r.status_code == 400 and "international format" in r.json()["detail"]
    assert ctx.saved == []


def test_intake_cleans_up_spaces_and_dashes_in_the_number(ctx):
    r = intake(ctx, "+92 300-1234567")
    assert r.status_code == 303
    args, kwargs = ctx.saved[0]
    assert args[0] == PHONE and kwargs["phone_number"] == PHONE


def test_intake_rejects_unsupported_file_types(ctx):
    r = intake(ctx, PHONE, name="summary.pdf", content=b"%PDF-1.4", mime="application/pdf")
    assert r.status_code == 400 and "Unsupported file type" in r.json()["detail"]
    assert ctx.saved == []


def test_intake_rejects_oversized_uploads(ctx):
    r = intake(ctx, PHONE, content=b"x" * (10 * 1024 * 1024 + 1))
    assert r.status_code == 413
    assert ctx.saved == []


def test_intake_accepts_a_file_exactly_at_the_limit(ctx):
    r = intake(ctx, PHONE, content=b"x" * (10 * 1024 * 1024))
    assert r.status_code == 303


# ---------------- Slow work must never freeze the server ----------------
def test_routes_that_do_slow_work_are_plain_functions(ctx):
    """An `async def` route running slow LLM/DB calls blocks the whole server, webhook included."""
    webhook = sys.modules["webhook"]
    for fn in (webhook.receive_message, webhook.intake_submit, webhook.process_message, webhook.dashboard):
        assert not asyncio.iscoroutinefunction(fn), fn.__name__

# ---------------- Resolve button ----------------
def test_resolve_needs_login(ctx):
    r = ctx.client.post("/dashboard/resolve/5", follow_redirects=False)
    assert r.status_code == 401


def test_resolve_marks_the_case_and_returns_to_the_dashboard(ctx, monkeypatch):
    resolved = []
    monkeypatch.setattr(sys.modules["webhook"], "resolve_message", resolved.append)
    r = ctx.client.post("/dashboard/resolve/5", auth=ADMIN, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/dashboard"
    assert resolved == [5]


def test_resolve_cannot_be_triggered_by_a_simple_link(ctx):
    assert ctx.client.get("/dashboard/resolve/5", auth=ADMIN).status_code == 405


def test_resolve_rejects_a_non_numeric_id(ctx):
    r = ctx.client.post("/dashboard/resolve/abc", auth=ADMIN, follow_redirects=False)
    assert r.status_code == 422