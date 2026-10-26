import hashlib
import hmac
import json
import logging

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from safe_logging import RedactPhoneFilter, mask_phone, patient_ref
from security import require_admin, require_meta_signature

APP_SECRET = "test-app-secret"
GOOD_AUTH = ("nurse", "s3cret!")


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "nurse")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret!")
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)


@pytest.fixture
def client():
    app = FastAPI()

    @app.get("/dashboard", dependencies=[Depends(require_admin)])
    def dashboard():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/meta/webhook")
    def webhook(raw: bytes = Depends(require_meta_signature)):
        return {"keys": sorted(json.loads(raw).keys())}

    return TestClient(app)


def sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---- Change 2: admin auth ----
def test_dashboard_requires_login(client):
    assert client.get("/dashboard").status_code == 401


def test_dashboard_wrong_password(client):
    assert client.get("/dashboard", auth=("nurse", "wrong")).status_code == 401


def test_dashboard_non_ascii_credentials_do_not_crash(client):
    assert client.get("/dashboard", auth=("nurse", "pässwörd")).status_code == 401


def test_dashboard_good_login(client):
    assert client.get("/dashboard", auth=GOOD_AUTH).status_code == 200


def test_health_is_public(client):
    assert client.get("/health").status_code == 200


def test_admin_fails_closed_when_unconfigured(client, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD")
    assert client.get("/dashboard", auth=GOOD_AUTH).status_code == 503


# ---- Change 3: webhook signature ----
BODY = json.dumps({"entry": [], "object": "whatsapp_business_account"}).encode()


def test_webhook_missing_signature(client):
    assert client.post("/meta/webhook", content=BODY).status_code == 403


def test_webhook_wrong_signature(client):
    r = client.post("/meta/webhook", content=BODY, headers={"X-Hub-Signature-256": sign(BODY, "other")})
    assert r.status_code == 403


def test_webhook_malformed_header(client):
    r = client.post("/meta/webhook", content=BODY, headers={"X-Hub-Signature-256": "garbage"})
    assert r.status_code == 403


def test_webhook_tampered_body(client):
    r = client.post("/meta/webhook", content=BODY + b" ", headers={"X-Hub-Signature-256": sign(BODY)})
    assert r.status_code == 403


def test_webhook_valid_signature(client):
    r = client.post("/meta/webhook", content=BODY, headers={"X-Hub-Signature-256": sign(BODY)})
    assert r.status_code == 200
    assert r.json() == {"keys": ["entry", "object"]}


def test_webhook_rejects_everything_if_secret_missing(client, monkeypatch):
    monkeypatch.delenv("META_APP_SECRET")
    r = client.post("/meta/webhook", content=BODY, headers={"X-Hub-Signature-256": sign(BODY)})
    assert r.status_code == 403


# ---- Change 1: logging ----
def test_mask_phone():
    assert mask_phone("+923001234567") == "***4567"
    assert "300123" not in mask_phone("+923001234567")


def test_patient_ref_is_stable_and_hides_number(monkeypatch):
    monkeypatch.setenv("LOG_HASH_SECRET", "k")
    a, b = patient_ref("+923001234567"), patient_ref("923001234567")
    assert a == b and "4567" not in a and len(a) == 10


def test_patient_ref_falls_back_to_mask_without_secret(monkeypatch):
    monkeypatch.delenv("LOG_HASH_SECRET", raising=False)
    assert patient_ref("+923001234567") == "***4567"


def test_filter_redacts_phone_numbers():
    rec = logging.LogRecord("t", logging.INFO, "f", 1, "from %s ok", ("+923001234567",), None)
    RedactPhoneFilter().filter(rec)
    assert "923001234567" not in rec.getMessage()
    assert "REDACTED" in rec.getMessage()


def test_filter_keeps_normal_ids():
    rec = logging.LogRecord("t", logging.INFO, "f", 1, "id=wamid.HBgLOTIzMDAxMjM0NTY3 risk=high", (), None)
    RedactPhoneFilter().filter(rec)
    assert rec.getMessage() == "id=wamid.HBgLOTIzMDAxMjM0NTY3 risk=high"