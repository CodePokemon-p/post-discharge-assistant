import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

PUSHOVER_API = "https://api.pushover.net/1/messages.json"


def send_pushover_notification(title: str, message: str, priority: int = 1) -> bool:
    """
    Send a push notification via Pushover. Returns True on success.
    Never raises -- a failed notification must not crash the pipeline.

    priority 1 = high priority (bypasses the phone's quiet hours).
    Callers should keep title/message free of symptom text and full phone
    numbers: this goes through a third party.
    Logs only the outcome, never the title, message, keys or API response.
    """
    token = os.getenv("PUSHOVER_APP_TOKEN")
    user = os.getenv("PUSHOVER_USER_KEY")

    if not token or not user:
        logger.info("pushover DRY RUN: no credentials configured, alert not sent")
        return False

    try:
        response = requests.post(
            PUSHOVER_API,
            data={
                "token": token,
                "user": user,
                "title": title[:100],        # Pushover title limit
                "message": message[:1024],   # Pushover message limit
                "priority": priority,
            },
            timeout=10,
        )
        if response.status_code == 200:
            logger.info("pushover alert sent")
            return True
        logger.warning("pushover alert failed status=%s", response.status_code)
        return False
    except Exception as e:
        # Log only the error type: exception text can echo request details.
        logger.warning("pushover error (non-fatal) type=%s", type(e).__name__)
        return False