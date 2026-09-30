"""
FastAPI webhook for Meta's WhatsApp Cloud API. Two real differences
from the Twilio version:

1. A GET endpoint is required. Meta calls it ONCE, when you first save
   the webhook URL in the developer dashboard, to prove you actually
   control this server it sends a challenge value we must echo back
   exactly, only if our verify token matches what you configured.

2. The POST payload is deeply nested JSON, not simple form fields, and
   Meta's webhook fires for MANY event types (message delivered, read
   receipts, etc.) not just new messages. We have to check the
   payload actually contains an inbound message before processing it.

RUNNING THIS:
1. uvicorn webhook:app --reload --port 8000
2. In a separate terminal: run cloud
3. In Meta Developer dashboard -> your app -> WhatsApp -> Configuration:
   Verify token = whatever you set META_VERIFY_TOKEN to in .env
   Click Verify and Save, then subscribe to the "messages" field.
4. Send a WhatsApp message to your Meta test number from one of the
   up to 5 numbers you registered as a test recipient.
"""

import os

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import PlainTextResponse, JSONResponse, HTMLResponse, RedirectResponse

from graph.build import build_graph
from storage import init_db, get_escalations
from messaging import send_whatsapp_message

app = FastAPI()
init_db()
graph_app = build_graph()

DEMO_PATIENT_ID = "patient_001"
DEMO_DISCHARGE_TEXT = open("data/sample_discharge.txt").read()

META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "discharge-assistant-verify")


@app.get("/meta/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == META_VERIFY_TOKEN:
        return PlainTextResponse(params.get("hub.challenge", ""))
    return PlainTextResponse("Verification failed", status_code=403)


@app.post("/meta/webhook")
async def receive_message(request: Request):
    payload = await request.json()

    try:
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    except (KeyError, IndexError):
        # Not an actual inbound message -- likely a delivery/read
        # receipt event. Acknowledge and ignore, don't error.
        return JSONResponse({"status": "ignored"})

    patient_number = "+" + message["from"]
    body = message.get("text", {}).get("body", "")
    print(f"[webhook] Inbound from {patient_number}: {body}")

    result = graph_app.invoke({
        "patient_id": patient_number,
        "discharge_text": DEMO_DISCHARGE_TEXT,
        "patient_message": body,
        "language": "en",
    })

    if result.get("answer"):
        reply_text = result["answer"]
    elif result.get("escalated"):
        reply_text = (
            "Thanks for letting us know. We've flagged this for your care "
            "team to review, and someone will follow up with you soon."
        )
    else:
        reply_text = "Got it, thanks for the update."

    if os.getenv("META_WHATSAPP_TOKEN") and os.getenv("META_PHONE_NUMBER_ID"):
        send_whatsapp_message(patient_number, reply_text)
        print(f"[webhook] Sent real reply to {patient_number}")
    else:
        print(f"[webhook] DRY RUN (no Meta credentials) -- would reply to {patient_number}:\n{reply_text}\n")

    return JSONResponse({"status": "ok"})


@app.get("/dashboard")
def dashboard():
    from storage import get_escalations, get_all_messages

    escalations = get_escalations()
    all_messages = get_all_messages()  # may or may not exist yet -- see note

    urgent = [r for r in escalations if str(r.get("priority", "")).lower() == "urgent"]
    low = [r for r in escalations if str(r.get("priority", "")).lower() != "urgent"]

    def render_rows(rows, border_color):
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

    all_html = []
    for r in all_messages:
        all_html.append(f"""
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
    all_messages_html = "\n".join(all_html) if all_html else "<p><em>None.</em></p>"

    return HTMLResponse(f"""
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
        </style>
    </head>
    <body>
        <h1>Nurse Dashboard</h1>
        <h2>URGENT ({len(urgent)})</h2>
        {render_rows(urgent, "#e53935")}
        <h2>LOW PRIORITY ({len(low)})</h2>
        {render_rows(low, "#fb8c00")}
        <h2>ALL MESSAGES ({len(all_messages)})</h2>
        {all_messages_html}
    </body>
    </html>
    """)
@app.get("/intake", response_class=HTMLResponse)
async def intake_form():
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Patient Intake</title></head>
    <body style="font-family: sans-serif; max-width: 600px; margin: 2rem auto;">
        <h1>New Patient Intake</h1>
        <form action="/intake" method="post" enctype="multipart/form-data">
            <label>Phone number (with country code, e.g. +923166568948):</label><br>
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
    import base64
    from graph.extraction import extract_care_plan_real, extract_text_from_image
    from storage import save_care_plan

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

    return RedirectResponse(url="/dashboard", status_code=303)