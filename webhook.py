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
1. uvicorn webhook_app:app --reload --port 8000
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
from fastapi.responses import PlainTextResponse, JSONResponse

from graph.build import build_graph
from storage import init_db
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