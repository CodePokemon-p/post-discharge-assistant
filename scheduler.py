"""
Scheduler: triggers a daily check-in message for every patient with a
saved care plan, via APScheduler.

WORKS WITHOUT META CREDENTIALS TOO -- READ THIS:
send_checkin() tries to send a real WhatsApp message via Meta's Cloud
API, but falls back to a dry run (nothing is sent) if META_WHATSAPP_TOKEN
isn't set in .env yet. Same "mock now, swap later" pattern used for the
LLM provider earlier. Once Meta credentials are set, this starts
sending real messages with zero code changes.

Logging rule: log WHAT HAPPENED, never WHAT WAS SAID. No full phone
numbers, medication names or message text in logs. To eyeball the
messages while testing locally, run `python scheduler.py` -- that path
prints previews to your own screen only.
"""

import logging
import os
import re

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

from graph.schemas import CarePlan
from safe_logging import mask_phone
from storage import get_all_patients

load_dotenv()

logger = logging.getLogger(__name__)


def _safe_id(patient_id) -> str:
    """Mask phone-number IDs; leave non-phone IDs (e.g. patient_001) alone."""
    pid = str(patient_id or "unknown")
    if len(re.sub(r"\D", "", pid)) >= 7:
        return mask_phone(pid)
    return pid


def _meta_ready() -> bool:
    # Read at call time (not import time) so the .env values are always seen.
    return bool(os.getenv("META_WHATSAPP_TOKEN") and os.getenv("META_PHONE_NUMBER_ID"))


def build_checkin_message(care_plan: CarePlan) -> str:
    """Compose a simple daily check-in message from a patient's care plan."""
    med_lines = "\n".join(f"- {m.name}: {m.schedule}" for m in care_plan.medications)
    return (
        "Hi, this is your post-discharge care team checking in. "
        "How are you feeling today?\n\n"
        f"Today's medication reminders:\n{med_lines}\n\n"
        "Reply to this message any time with questions or to let us "
        "know how you're feeling."
    )


def send_checkin(
    patient_id: str,
    phone_number: str,
    care_plan: CarePlan,
    preview: bool = False,
) -> None:
    """
    preview=True prints the message to the local screen (manual testing only).
    The scheduled daily job never uses it.
    """
    message = build_checkin_message(care_plan)
    ref = _safe_id(patient_id)

    if preview:
        print(f"\n--- check-in preview for {ref} ---\n{message}\n")

    if _meta_ready() and phone_number:
        from messaging import send_whatsapp_message
        send_whatsapp_message(phone_number, message)
        logger.info("check-in sent patient=%s", ref)
    else:
        reason = (
            "no Meta credentials"
            if not _meta_ready()
            else "no phone_number on file"
        )
        logger.info("check-in DRY RUN (%s) patient=%s", reason, ref)


def run_all_checkins_now(preview: bool = False) -> None:
    """
    Sends (or dry-runs) a check-in for every patient with a saved
    care plan. Callable directly for testing, without waiting for a
    real daily trigger to fire.
    """
    patients = get_all_patients()
    logger.info("running check-ins for %d patient(s)", len(patients))
    for patient in patients:
        try:
            send_checkin(
                patient["patient_id"],
                patient["phone_number"],
                patient["care_plan"],
                preview=preview,
            )
        except Exception as e:
            # One patient's failure must not stop everyone else's check-in.
            # Log only the error type: exception text can echo request details.
            logger.warning(
                "check-in failed patient=%s type=%s",
                _safe_id(patient.get("patient_id")), type(e).__name__,
            )


def start_scheduler() -> BackgroundScheduler:
    """
    Starts a background job that runs check-ins once a day. Call this
    once when the webhook server starts up.
    """
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_all_checkins_now, "cron", hour=9, minute=0)  # 9:00 AM daily
    scheduler.start()
    logger.info("background scheduler started: daily check-ins at 09:00")
    return scheduler


if __name__ == "__main__":
    # Manual test entry point: python scheduler.py runs check-ins for
    # every patient immediately, without waiting for 9 AM, and prints
    # the messages to your screen so you can read them.
    from safe_logging import setup_logging

    setup_logging()
    run_all_checkins_now(preview=True)