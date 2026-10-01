"""
FastAPI webhook for Meta's WhatsApp Cloud API + Admin intake + Nurse dashboard.

Architecture (correct, non-demo):
  * Admin uploads a discharge summary through /intake (with a phone number).
  * System extracts the care plan and saves it to the DB, keyed by phone.
  * When that patient messages on WhatsApp, the webhook looks up their
    care plan by phone number and runs the graph with it.
  * No files are read at startup. No hardcoded demo patient. Every
    WhatsApp sender is treated as their own patient.

RUNNING:
1. uvicorn webhook:app --reload --port 8000
2. cloudflared tunnel --url http://localhost:8000
3. Meta dashboard -> WhatsApp -> Configuration -> Webhook:
     Callback URL = https://<tunnel>/meta/webhook
     Verify token = META_VERIFY_TOKEN from .env
4. Admin: open http://localhost:8000/intake to register a patient.
5. Patient: send a WhatsApp message from their phone.
"""

import base64
import os

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (
    PlainTextResponse,
    JSONResponse,
    HTMLResponse,
    RedirectResponse,
)

from graph.build import build_graph
from storage import init_db, get_care_plan, save_care_plan, get_escalations, get_all_messages
from messaging import send_whatsapp_message

app = FastAPI()
init_db()
graph_app = build_graph()

META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "discharge-assistant-verify")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Meta WhatsApp webhook
# ---------------------------------------------------------------------------

@app.get("/meta/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == META_VERIFY_TOKEN
    ):
        return PlainTextResponse(params.get("hub.challenge", ""))
    return PlainTextResponse("Verification failed", status_code=403)


@app.post("/meta/webhook")
async def receive_message(request: Request):
    payload = await request.json()

    try:
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    except (KeyError, IndexError):
        # Delivery/read receipts and other non-message events. Ignore.
        return JSONResponse({"status": "ignored"})

    patient_number = "+" + message["from"]
    body = message.get("text", {}).get("body", "")
    print(f"[webhook] Inbound from {patient_number}: {body}")

    # Look up this patient's care plan by phone number.
    care_plan = get_care_plan(patient_number)
    if care_plan is None:
        print(f"[webhook] No care plan on file for {patient_number} -- declining politely.")
        reply_text = (
            "We don't have your discharge information on file yet. "
            "Please contact your care team to get registered."
        )
        if os.getenv("META_WHATSAPP_TOKEN") and os.getenv("META_PHONE_NUMBER_ID"):
            send_whatsapp_message(patient_number, reply_text)
        return JSONResponse({"status": "unregistered_patient"})

    # Run the graph with the stored care plan. No file reads, no demo data.
    result = graph_app.invoke({
        "patient_id": patient_number,
        "care_plan": care_plan,                       # already loaded from DB
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

    if os.getenv("META_WHATSAPP_TOKEN") and os.getenv("META_PHONE_NUMBER_ID"):
        send_whatsapp_message(patient_number, reply_text)
        print(f"[webhook] Sent real reply to {patient_number}")
    else:
        print(
            f"[webhook] DRY RUN (no Meta credentials) -- would reply to "
            f"{patient_number}:\n{reply_text}\n"
        )

    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Admin intake
# ---------------------------------------------------------------------------

@app.get("/intake", response_class=HTMLResponse)
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
            <label>Patient's WhatsApp number (with country code, e.g. +923166568948):</label><br>
            <input type="text" name="phone_number" required
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


@app.post("/intake")
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
    print(f"[intake] Registered {phone_number} with diagnosis: {care_plan.diagnosis}")

    return RedirectResponse(url="/dashboard", status_code=303)


# ---------------------------------------------------------------------------
# Nurse dashboard
# ---------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    escalations = get_escalations()
    all_messages = get_all_messages()

    urgent = [r for r in escalations if str(r.get("priority", "")).lower() == "urgent"]
    low = [r for r in escalations if str(r.get("priority", "")).lower() != "urgent"]

    def render_escalation_rows(rows, border_color):
        if not rows:
            return "<p><em>None.</em></p>"
        html = []
        for r in rows:
            html.append(f"""
            <div class="case" style="border-left-color: {border_color};">
                <div><strong>Patient:</strong> {r.get('patient_id', '?')}
                     &nbsp;|&nbsp;
                     <strong>Risk:</strong> {r.get('risk_level', 'n/a')}</div>
                <div class="msg"><strong>Message:</strong> {r.get('content', '')}</div>
                <div class="reason"><strong>Reason:</strong> {r.get('reasoning', '')}</div>
            </div>
            """)
        return "\n".join(html)

    def render_all_rows(rows):
        if not rows:
            return "<p><em>None.</em></p>"
        html = []
        for r in rows:
            html.append(f"""
            <div class="case" style="border-left-color: #90a4ae;">
                <div><strong>Patient:</strong> {r.get('patient_id', '?')}
                     &nbsp;|&nbsp;
                     <strong>Intent:</strong> {r.get('intent', '?')}
                     &nbsp;|&nbsp;
                     <strong>Escalated:</strong> {r.get('escalated', '?')}</div>
                <div class="msg"><strong>Message:</strong> {r.get('content', '')}</div>
                <div class="reason"><strong>Answer:</strong> {r.get('answer', '(none)')}</div>
            </div>
            """)
        return "\n".join(html)

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