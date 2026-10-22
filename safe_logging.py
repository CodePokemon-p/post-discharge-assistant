"""Safe logging helpers: keep patient phone numbers and message text out of logs.

Rules this module supports:
  * Log events and IDs, never message bodies, care plans, or LLM prompts/replies.
  * Refer to patients by mask_phone() or patient_ref(), never the raw number.
  * RedactPhoneFilter is a safety net that scrubs any phone-like number that
    slips into a log line anyway.
"""
import hashlib
import hmac
import logging
import os
import re

# 10-15 digits, optional leading +, not part of a longer word/ID.
_PHONE_RE = re.compile(r"(?<![\w.])\+?\d{10,15}(?![\w.])")


def mask_phone(number: str) -> str:
    """'+923001234567' -> '***4567' (last 4 digits only)."""
    digits = re.sub(r"\D", "", number or "")
    return f"***{digits[-4:]}" if len(digits) >= 4 else "***"


def patient_ref(number: str) -> str:
    """Stable pseudonymous tag so you can match log lines to one patient.

    Uses HMAC with LOG_HASH_SECRET. Without a secret a plain hash of a phone
    number is easy to brute-force, so we fall back to the masked form.
    """
    secret = os.getenv("LOG_HASH_SECRET", "")
    if not secret:
        return mask_phone(number)
    digits = re.sub(r"\D", "", number or "")
    return hmac.new(secret.encode(), digits.encode(), hashlib.sha256).hexdigest()[:10]


class RedactPhoneFilter(logging.Filter):
    """Replace anything that looks like a phone number in the final log message."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _PHONE_RE.sub("***REDACTED***", record.getMessage())
        record.args = ()
        return True


def setup_logging() -> None:
    """Call once at startup, before creating the FastAPI app."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactPhoneFilter())
    # HTTP client libraries log full request URLs at INFO; keep them quiet.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)