"""
FastAPI webhook for Meta's WhatsApp Cloud API + Admin intake + Nurse dashboard.

Architecture:
  * Admin uploads a discharge summary through /intake (with a phone number).
  * System extracts the care plan and saves it to the DB, keyed by phone.
  * When that patient messages on WhatsApp, the webhook answers Meta
    IMMEDIATELY ("accepted"), then does the slow work (graph + reply) in
    the background.
  * Every WhatsApp message ID is claimed in the database first, so a
    retry or duplicate from Meta is skipped (idempotency).
  * No files are read at startup. No hardcoded demo patient. Every
    WhatsApp sender is treated as their own patient.

SECURITY (Phase A):
  * /dashboard and /intake require HTTP Basic auth (ADMIN_USER / ADMIN_PASSWORD).
  * POST /meta/webhook requires a valid X-Hub-Signature-256 (META_APP_SECRET).
  * GET /meta/webhook uses META_VERIFY_TOKEN (no default; must be set).
  * Logs never contain message text, care plans, diagnoses or full phone numbers.
  * Everything rendered on the dashboard is HTML-escaped.

RELIABILITY (Phase B):
  * Meta gets its "200 OK" in milliseconds; the graph runs afterwards.
  * Duplicate deliveries are skipped via claim_message().
  * If background processing fails, the failure is NOT silent: the message is
    put on the dashboard as urgent, a nurse is alerted, and the patient gets
    a safe fallback reply.

RUNNING:
1. uvicorn webhook:app --reload --port 8000
2. cloudflared tunnel --url http://localhost:8000
3. Meta dashboard -> WhatsApp -> Configuration -> Webhook:
     Callback URL = https://<tunnel>/meta/webhook
     Verify token = META_VERIFY_TOKEN from .env
4. Admin: open http://localhost:8000/intake (browser asks for the admin login).
5. Patient: send a WhatsApp message from their phone.
"""

import base64
import html
import json
import logging
import os
import secrets
import traceback

from dotenv import load_dotenv

load_dotenv()  # before any module that reads env vars at import time

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (
    PlainTextResponse,
    JSONResponse,
    HTMLResponse,
    RedirectResponse,
)

from graph.build import build_graph
from storage import (
    init_db,
    get_care_plan,
    save_care_plan,
    get_escalations,
    get_all_messages,
    claim_message,
    log_message,
)
from messaging import send_whatsapp_message
from pushover_notify import send_pushover_notification
from safe_logging import mask_phone, patient_ref, setup_logging
from security import require_admin, require_meta_signature

setup_logging()
logger = logging.getLogger("webhook")

app = FastAPI()
init_db()
graph_app = build_graph()

FALLBACK_REPLY = (
    "Sorry, we couldn't process your message just now. A member of your care "
    "team has been notified. If this is urgent or you feel very unwell, please "
    "contact your care team or emergency services directly."
)



# Helpers

def _care_plan_to_text(care_plan) -> str:
    """
    Reconstruct a readable discharge summary from a stored CarePlan.
    Used by the WhatsApp path because we don't keep the original raw
    document text -- only the extracted structured plan. The answering
    agent gets this as its document context.
    """
    meds = "\n".join(f"- {m.name}: {m.schedule}" for m in care_plan.medications) or "- (none listed)"
    flags = "\n".join(f"- {f}" for f in care_plan.red_flags) or "- (none listed)"
    return (
        f"DISCHARGE SUMMARY\n\n"
        f"Diagnosis: {care_plan.diagnosis}\n\n"
        f"Medications:\n{meds}\n\n"
        f"Follow-up: {care_plan.follow_up_date or '(not specified)'}\n\n"
        f"Red-flag symptoms to watch for:\n{flags}\n"
    )


def _esc(value) -> str:
    """HTML-escape anything user- or LLM-controlled before putting it in a page."""
    return html.escape("" if value is None else str(value))


def _meta_configured() -> bool:
    return bool(os.getenv("META_WHATSAPP_TOKEN") and os.getenv("META_PHONE_NUMBER_ID"))



# Meta WhatsApp webhook


@app.get("/meta/webhook")
async def verify_webhook(request: Request):
    """One-time handshake. Uses the verify token (NOT the app secret)."""
    params = request.query_params
    expected = os.getenv("META_VERIFY_TOKEN", "")
    supplied = params.get("hub.verify_token", "")
    if (
        expected
        and params.get("hub.mode") == "subscribe"
        and secrets.compare_digest(supplied.encode(), expected.encode())
    ):
        return PlainTextResponse(params.get("hub.challenge", ""))
    return PlainTextResponse("Verification failed", status_code=403)


def _handle_processing_failure(message: dict, patient_number, body: str, error: Exception) -> None:
    """
    Background work failed AFTER we told Meta "200 OK", so Meta will not retry.
    That means a failure here would be completely silent -- and the lost
    message might be an emergency. So fail toward safety:
      1. record it on the dashboard as urgent (a human must look at it),
      2. alert a nurse,
      3. give the patient a safe fallback reply.
    Logs carry only the error type and where it happened -- never content.
    """
    tb = traceback.extract_tb(error.__traceback__)
    where = f"{os.path.basename(tb[-1].filename)}:{tb[-1].lineno}" if tb else "unknown"
    ref = patient_ref(patient_number) if patient_number else "unknown"
    logger.error(
        "processing failed msg_id=%s patient=%s type=%s at=%s",
        message.get("id"), ref, type(error).__name__, where,
    )

    if patient_number:
        try:
            log_message(
                patient_id=patient_number,
                content=body,
                escalated=True,
                priority="urgent",
                reasoning="Automatic processing failed -- needs human review.",
            )
        except Exception as e:
            logger.warning("could not record failed message for review type=%s", type(e).__name__)

    # Never raises (see pushover_notify.py).
    send_pushover_notification(
        title=f"URGENT: message needs manual review (patient {mask_phone(patient_number or '')})",
        message="Automatic processing failed. Open the nurse dashboard for details.",
    )

    if patient_number and _meta_configured():
        try:
            send_whatsapp_message(patient_number, FALLBACK_REPLY)
        except Exception as e:
            logger.warning("could not send fallback reply patient=%s type=%s", ref, type(e).__name__)


def process_message(message: dict) -> None:
    """
    The slow part: look up the care plan, run the graph, send the reply.
    Runs in the background after Meta has already been answered. Never raises.
    """
    patient_number = None
    body = ""
    try:
        patient_number = "+" + message["from"]
        body = message.get("text", {}).get("body", "")
        ref = patient_ref(patient_number)
        logger.info(
            "processing message patient=%s msg_id=%s type=%s",
            ref, message.get("id"), message.get("type"),
        )

        # Look up this patient's care plan by phone number.
        care_plan = get_care_plan(patient_number)
        if care_plan is None:
            logger.info("no care plan on file patient=%s -- declining politely", ref)
            reply_text = (
                "We don't have your discharge information on file yet. "
                "Please contact your care team to get registered."
            )
            if _meta_configured():
                send_whatsapp_message(patient_number, reply_text)
            return

        # Run the graph with the stored care plan. No file reads, no demo data.
        result = graph_app.invoke({
            "patient_id": patient_number,
            "care_plan": care_plan,                           # already loaded from DB
            "discharge_text": _care_plan_to_text(care_plan),  # reconstructed for the answer agent
            "patient_message": body,
            "language": "en",
        })

        if result.get("answer"):
            reply_text = result["answer"]
        elif result.get("escalated"):
            reply_text = (
                "Thanks for letting us know. We've flagged this for your "
                "care team to review, and someone will follow up with you soon."
            )
        else:
            reply_text = "Got it, thanks for the update."

        if _meta_configured():
            send_whatsapp_message(patient_number, reply_text)
            logger.info("reply sent patient=%s escalated=%s", ref, bool(result.get("escalated")))
        else:
            logger.info("DRY RUN (no Meta credentials): reply not sent patient=%s", ref)

    except Exception as e:
        _handle_processing_failure(message, patient_number, body, e)


# Plain `def` (not async): FastAPI runs it in a threadpool, and the background
# task (also a plain function) runs in the threadpool too, so neither the
# database call here nor the slow graph call freezes the event loop.
@app.post("/meta/webhook")
def receive_message(
    background: BackgroundTasks,
    raw: bytes = Depends(require_meta_signature),
):
    # `raw` is the signature-verified request body.
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return JSONResponse({"status": "bad_request"}, status_code=400)

    try:
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    except (KeyError, IndexError, TypeError):
        # Delivery/read receipts and other non-message events. Ignore.
        return JSONResponse({"status": "ignored"})

    message_id = message.get("id")
    if not message_id:
        logger.warning("webhook message without an id -- ignored")
        return JSONResponse({"status": "ignored"})

    # Idempotency: claim this message ID. If the database is down this raises,
    # the route returns 500, and Meta retries later -- which is what we want,
    # because we have NOT accepted the message.
    if not claim_message(message_id):
        logger.info("duplicate message skipped msg_id=%s", message_id)
        return JSONResponse({"status": "duplicate"})

    background.add_task(process_message, message)
    return JSONResponse({"status": "accepted"})



# Admin intake (protected)


@app.get("/intake", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def intake_form():
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Patient Intake</title></head>
    <body style="font-family: sans-serif; max-width: 600px; margin: 2rem auto;">
        <h1>New Patient Intake</h1>
        <p>Upload a discharge summary for a patient. Once registered, the
           patient can message on WhatsApp and get responses based on this plan.</p>
        <form action="/intake" method="post" enctype="multipart/form-data">
            <label>Patient's WhatsApp number (with country code):</label><br>
            <input type="text" name="phone_number" required
                   placeholder="+92XXXXXXXXXX"
                   style="width: 100%; padding: 0.5rem; margin: 0.5rem 0;"><br>
            <label>Discharge summary (image or text file):</label><br>
            <input type="file" name="file" required
                   accept=".txt,.jpg,.jpeg,.png"
                   style="margin: 0.5rem 0;"><br>
            <button type="submit" style="padding: 0.5rem 1rem;">Register Patient</button>
        </form>
    </body>
    </html>
    """


@app.post("/intake", dependencies=[Depends(require_admin)])
async def intake_submit(
    phone_number: str = Form(...),
    file: UploadFile = File(...),
):
    from graph.extraction import extract_care_plan_real, extract_text_from_image

    content = await file.read()
    filename = (file.filename or "").lower()

    if filename.endswith((".jpg", ".jpeg", ".png")):
        mime = "image/jpeg" if filename.endswith((".jpg", ".jpeg")) else "image/png"
        b64 = base64.b64encode(content).decode("ascii")
        discharge_text = extract_text_from_image(b64, mime)
    else:
        discharge_text = content.decode("utf-8", errors="replace")

    care_plan = extract_care_plan_real(discharge_text)
    save_care_plan(
        phone_number,
        care_plan,
        language="en",
        phone_number=phone_number,
    )
    # Never log the diagnosis or the full number.
    logger.info("intake registered patient=%s", patient_ref(phone_number))

    return RedirectResponse(url="/dashboard", status_code=303)



# Nurse dashboard (protected)


@app.get("/health")
def health():
    """Public liveness check for uptime monitors. Returns no data."""
    return {"status": "ok"}


@app.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def dashboard():
    escalations = get_escalations()
    all_messages = get_all_messages()

    urgent = [r for r in escalations if str(r.get("priority", "")).lower() == "urgent"]
    low = [r for r in escalations if str(r.get("priority", "")).lower() != "urgent"]

    # Patient messages and LLM output are untrusted: every value is escaped,
    # otherwise a message like "<script>..." would run in the nurse's browser.
    def render_escalation_rows(rows, border_color):
        if not rows:
            return "<p><em>None.</em></p>"
        out = []
        for r in rows:
            out.append(f"""
            <div class="case" style="border-left-color: {border_color};">
                <div><strong>Patient:</strong> {_esc(r.get('patient_id', '?'))}
                     &nbsp;|&nbsp;
                     <strong>Risk:</strong> {_esc(r.get('risk_level', 'n/a'))}</div>
                <div class="msg"><strong>Message:</strong> {_esc(r.get('content', ''))}</div>
                <div class="reason"><strong>Reason:</strong> {_esc(r.get('reasoning', ''))}</div>
            </div>
            """)
        return "\n".join(out)

    def render_all_rows(rows):
        if not rows:
            return "<p><em>None.</em></p>"
        out = []
        for r in rows:
            out.append(f"""
            <div class="case" style="border-left-color: #90a4ae;">
                <div><strong>Patient:</strong> {_esc(r.get('patient_id', '?'))}
                     &nbsp;|&nbsp;
                     <strong>Intent:</strong> {_esc(r.get('intent', '?'))}
                     &nbsp;|&nbsp;
                     <strong>Escalated:</strong> {_esc(r.get('escalated', '?'))}</div>
                <div class="msg"><strong>Message:</strong> {_esc(r.get('content', ''))}</div>
                <div class="reason"><strong>Answer:</strong> {_esc(r.get('answer') or '(none)')}</div>
            </div>
            """)
        return "\n".join(out)

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Nurse Dashboard</title>
        <style>
            body {{ font-family: sans-serif; margin: 2rem; background: #f7f7f9; }}
            h1 {{ color: #1a237e; }}
            h2 {{ color: #333; margin-top: 2rem; }}
            .case {{ background: #fff; padding: 1rem; margin: 1rem 0;
                     border-left: 4px solid #e53935; border-radius: 4px; }}
            .msg {{ margin-top: .5rem; color: #333; }}
            .reason {{ margin-top: .3rem; color: #666; font-size: 0.9em; }}
            nav a {{ margin-right: 1rem; }}
        </style>
    </head>
    <body>
        <nav><a href="/intake">+ Register New Patient</a></nav>
        <h1>Nurse Dashboard</h1>
        <h2>URGENT ({len(urgent)})</h2>
        {render_escalation_rows(urgent, "#e53935")}
        <h2>LOW PRIORITY ({len(low)})</h2>
        {render_escalation_rows(low, "#fb8c00")}
        <h2>ALL MESSAGES ({len(all_messages)})</h2>
        {render_all_rows(all_messages)}
    </body>
    </html>
    """