import os
from dotenv import load_dotenv

load_dotenv()  # reads the .env file in your project root, if present

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


def require_api_key():
    """
    Call this at the top of any node that will make a REAL Claude call.
    This keeps the mock pipeline runnable with zero key (like right now),
    but fails immediately with a clear message the moment a real node
    tries to run without one -- instead of a confusing error from deep
    inside the Anthropic SDK.
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Copy .env.example to .env and "
            "add your real key before running real Claude calls."
        )