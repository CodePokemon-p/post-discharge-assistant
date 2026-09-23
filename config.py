import os
from dotenv import load_dotenv

load_dotenv()  # reads the .env file in your project root, if present

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")  # "anthropic" | "groq"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")


def require_api_key():
    """
    Call this at the top of any node that will make a REAL LLM call.
    Only checks the key that the ACTIVE provider actually needs -- so
    testing with LLM_PROVIDER=groq never demands an Anthropic key, and
    vice versa. Fails immediately with a clear message instead of a
    confusing error from deep inside an SDK.
    """
    if LLM_PROVIDER == "anthropic" and not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "LLM_PROVIDER is 'anthropic' but ANTHROPIC_API_KEY is not set. "
            "Add it to your .env file."
        )
    if LLM_PROVIDER == "groq" and not GROQ_API_KEY:
        raise RuntimeError(
            "LLM_PROVIDER is 'groq' but GROQ_API_KEY is not set. "
            "Add it to your .env file."
        )