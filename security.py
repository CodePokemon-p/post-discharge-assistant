"""Security dependencies for the FastAPI app.

  * require_admin          -> HTTP Basic auth for /dashboard and /intake
  * require_meta_signature -> verifies X-Hub-Signature-256 on POST /meta/webhook

Env vars: ADMIN_USER, ADMIN_PASSWORD, META_APP_SECRET
(META_APP_SECRET is the Meta *App Secret*, not the webhook verify token.)
"""
import hashlib
import hmac
import logging
import os
import secrets
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

logger = logging.getLogger(__name__)
_basic = HTTPBasic()


def require_admin(credentials: HTTPBasicCredentials = Depends(_basic)) -> str:
    """Fail closed: if credentials are not configured, nobody gets in."""
    expected_user = os.getenv("ADMIN_USER")
    expected_pass = os.getenv("ADMIN_PASSWORD")
    if not expected_user or not expected_pass:
        logger.error("ADMIN_USER / ADMIN_PASSWORD not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin authentication is not configured",
        )
    # Constant-time comparison (on bytes, so non-ASCII input cannot crash it).
    user_ok = secrets.compare_digest(credentials.username.encode(), expected_user.encode())
    pass_ok = secrets.compare_digest(credentials.password.encode(), expected_pass.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def verify_meta_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """True only if the header equals sha256=HMAC(app_secret, raw_body)."""
    app_secret = os.getenv("META_APP_SECRET")
    if not app_secret:
        logger.error("META_APP_SECRET not configured; rejecting webhook POST")
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header[len("sha256="):])


async def require_meta_signature(request: Request) -> bytes:
    """Dependency: returns the verified RAW body bytes.

    Verify BEFORE parsing JSON: re-serializing parsed JSON changes the bytes
    and breaks the signature. In the route, use json.loads(raw).
    """
    raw = await request.body()
    if not verify_meta_signature(raw, request.headers.get("X-Hub-Signature-256")):
        logger.warning("Rejected webhook POST: missing or invalid signature")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")
    return raw