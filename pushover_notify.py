import os
import requests
from dotenv import load_dotenv

load_dotenv()  # ← ADD THIS LINE

PUSHOVER_API = "https://api.pushover.net/1/messages.json"


def send_pushover_notification(title: str, message: str) -> bool:
    """
    Send a push notification via Pushover. Returns True on success.
    Never raises -- a failed notification must not crash the pipeline.
    """
    token = os.getenv("PUSHOVER_APP_TOKEN")
    user = os.getenv("PUSHOVER_USER_KEY")

    if not token or not user:
        print("[pushover] DRY RUN (no credentials) -- would send:", title, "|", message)
        return False

    try:
        response = requests.post(
            PUSHOVER_API,
            data={
                "token": token,
                "user": user,
                "title": title[:100],          # Pushover title limit
                "message": message[:1024],      # Pushover message limit [citation:1]
                "priority": 0,                  # high priority -- bypasses quiet hours
            },
            timeout=10,
        )
        if response.status_code == 200:
            print("[pushover] Push sent successfully.")
            return True
        else:
            print("[pushover] Failed:", response.text)
            return False
    except Exception as e:
        print("[pushover] Error (non-fatal):", e)
        return False