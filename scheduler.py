"""
Scheduler: triggers a daily check-in message for every patient with a
saved care plan, via APScheduler.

WORKS WITHOUT META CREDENTIALS TOO -- READ THIS:
send_checkin() tries to send a real WhatsApp message via Meta's Cloud
API, but falls back to printing the message if META_WHATSAPP_TOKEN
isn't set in .env yet. Same "mock now, swap later" pattern used for the
LLM provider earlier. Once Meta credentials are set, this starts
sending real messages with zero code changes.
"""

import os

from apscheduler.schedulers.background import BackgroundScheduler

from graph.schemas import CarePlan
from storage import get_all_patients

META_WHATSAPP_TOKEN = os.getenv("META_WHATSAPP_TOKEN")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")


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


def send_checkin(patient_id: str, phone_number: str, care_plan: CarePlan) -> None:
    message = build_checkin_message(care_plan)

    if META_WHATSAPP_TOKEN and META_PHONE_NUMBER_ID and phone_number:
        from messaging import send_whatsapp_message
        send_whatsapp_message(phone_number, message)
        print(f"[scheduler] Sent real check-in to {patient_id} ({phone_number})")
    else:
        reason = (
            "no Meta credentials"
            if not (META_WHATSAPP_TOKEN and META_PHONE_NUMBER_ID)
            else "no phone_number on file"
        )
        print(f"[scheduler] DRY RUN ({reason}) -- would message {patient_id}:\n{message}\n")


def run_all_checkins_now() -> None:
    """
    Sends (or dry-run prints) a check-in for every patient with a saved
    care plan. Callable directly for testing, without waiting for a
    real daily trigger to fire.
    """
    patients = get_all_patients()
    print(f"[scheduler] Running check-ins for {len(patients)} patient(s)...")
    for patient in patients:
        send_checkin(patient["patient_id"], patient["phone_number"], patient["care_plan"])


def start_scheduler() -> BackgroundScheduler:
    """
    Starts a background job that runs check-ins once a day. Call this
    once when the webhook server starts up.
    """
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_all_checkins_now, "cron", hour=9, minute=0)  # 9:00 AM daily
    scheduler.start()
    print("[scheduler] Background scheduler started -- daily check-ins at 9:00 AM.")
    return scheduler


if __name__ == "__main__":
    # Manual test entry point: python scheduler.py runs check-ins for
    # every patient immediately, without waiting for 9 AM.
    run_all_checkins_now()