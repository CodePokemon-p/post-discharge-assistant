"""
FastAPI webhook for Meta's WhatsApp Cloud API. Two real differences
from the Twilio version:

1. A GET endpoint is required. Meta calls it ONCE, when you first save
   the webhook URL in the developer dashboard, to prove you actually
   control this server -- it sends a challenge value we must echo back
   exactly, only if our verify token matches what you configured.

2. The POST payload is deeply nested JSON, not simple form fields, and
   Meta's webhook fires for MANY event types (message delivered, read
   receipts, etc.) -- not just new messages. We have to check the
   payload actually contains an inbound message before processing it.

RUNNING THIS:
1. uvicorn webhook:app --reload --port 8000
2. In a separate terminal: ngrok http 8000
3. In Meta Developer dashboard -> your app -> WhatsApp -> Configuration:
   Callback URL = your ngrok https URL + /meta/webhook
   Verify token = whatever you set META_VERIFY_TOKEN to in .env
   Click Verify and Save, then subscribe to the "messages" field.
4. Send a WhatsApp message to your Meta test number from one of the
   up to 5 numbers you registered as a test recipient.
"""

import os

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse, HTMLResponse

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
        "patient_id": DEMO_PATIENT_ID,
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


@app.get("/dashboard", response_class=HTMLResponse)
async def nurse_dashboard():
    """
    Minimal nurse dashboard: lists every escalated case, newest first.
    Auto-refreshes every 10 seconds so a demo recording can show a new
    WhatsApp message appear here live, without manually reloading.
    """
    escalations = get_escalations()

    rows = ""
    for e in escalations:
        risk = e["risk_level"] or "N/A"
        risk_class = f"risk-{risk.lower()}" if e["risk_level"] else "risk-na"
        rows += f"""
        <tr>
            <td>{e['patient_id']}</td>
            <td>{e['content']}</td>
            <td><span class="{risk_class}">{risk}</span></td>
            <td>{e['reasoning'] or '-'}</td>
            <td>{e['timestamp']}</td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="5" style="text-align:center;color:#888;">No escalations yet.</td></tr>'

    html = f"""
    <html>
    <head>
        <title>Nurse Escalation Dashboard</title>
        <meta http-equiv="refresh" content="10">
        <style>
            body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; background: #f5f6fa; padding: 32px; }}
            h1 {{ color: #1a1a2e; margin-bottom: 4px; }}
            .subtitle {{ color: #666; margin-bottom: 20px; }}
            table {{ width: 100%; border-collapse: collapse; background: white;
                     box-shadow: 0 1px 6px rgba(0,0,0,0.08); border-radius: 8px; overflow: hidden; }}
            th, td {{ text-align: left; padding: 12px 16px; border-bottom: 1px solid #eee; }}
            th {{ background: #1a1a2e; color: white; font-weight: 600; }}
            tr:last-child td {{ border-bottom: none; }}
            .risk-high {{ background: #e74c3c; color: white; padding: 4px 10px; border-radius: 4px; font-weight: 600; }}
            .risk-low {{ background: #27ae60; color: white; padding: 4px 10px; border-radius: 4px; }}
            .risk-na {{ background: #95a5a6; color: white; padding: 4px 10px; border-radius: 4px; }}
        </style>
    </head>
    <body>
        <h1>Post-Discharge Nurse Dashboard</h1>
        <p class="subtitle">{len(escalations)} escalated case(s) &mdash; refreshes automatically every 10 seconds</p>
        <table>
            <tr><th>Patient</th><th>Message</th><th>Risk</th><th>Reason</th><th>Time</th></tr>
            {rows}
        </table>
    </body>
    </html>
    """
    return html