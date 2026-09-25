"""
WhatsApp messaging via Meta's WhatsApp Cloud API, direct -- no Twilio.

WHY THIS INSTEAD OF TWILIO:
Twilio's SMS-based signup verification blocked this account for
geographic fraud-prevention reasons. Meta's Cloud API is the actual
underlying platform Twilio wraps -- signup goes through a Facebook
Developer account instead of phone-based SMS verification, avoiding
that specific block. It's also, if anything, one layer closer to
production than going through a third-party wrapper.

SAME FUNDAMENTAL RULE AS BEFORE: free-form text only works within a
24-hour window after the recipient's last message to you. Outside that
window, only pre-approved templates work. This is a WhatsApp platform
rule, not specific to Twilio or Meta -- it applies no matter which
provider sits underneath.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()  # self-contained: don't rely on some other module having
                # already loaded .env first, regardless of import order

META_WHATSAPP_TOKEN = os.getenv("META_WHATSAPP_TOKEN")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
META_API_VERSION = os.getenv("META_API_VERSION", "v21.0")


def _require_credentials() -> None:
    if not META_WHATSAPP_TOKEN or not META_PHONE_NUMBER_ID:
        raise RuntimeError(
            "META_WHATSAPP_TOKEN / META_PHONE_NUMBER_ID not set. "
            "Add them to your .env file before sending real messages."
        )


def send_whatsapp_message(to_number: str, body: str) -> str:
    """
    Sends a free-form WhatsApp message via Meta's Cloud API.

    to_number: recipient's number, with or without a leading '+' --
               Meta's API wants it WITHOUT one, so we strip it here.
    Returns Meta's message id on success. Raises for any HTTP error
    (invalid token, unregistered test number, outside the 24h window
    with non-template text, etc.) -- never silently swallow a failed send.
    """
    _require_credentials()

    url = f"https://graph.facebook.com/{META_API_VERSION}/{META_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number.lstrip("+"),
        "type": "text",
        "text": {"body": body},
    }

    response = requests.post(url, headers=headers, json=payload, timeout=10)
    response.raise_for_status()
    return response.json()["messages"][0]["id"]