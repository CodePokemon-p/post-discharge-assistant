"""
Provider-agnostic structured LLM calls.

WHY THIS FILE EXISTS:
Without it, extraction.py, triage.py, and answering.py would each call
`anthropic.Anthropic(...)` directly -- meaning switching providers (or
even just testing against a free model while waiting on a paid key)
means editing three files. That's tight coupling to one vendor's SDK,
and it's a real production smell.

With this file, every node calls ONE function -- call_structured() --
and never imports anthropic or openai directly. Switching providers is
a single environment variable change (LLM_PROVIDER=anthropic vs
LLM_PROVIDER=groq), everywhere, at once.

This is the same idea behind tools like LiteLLM in real production
systems: depend on an abstraction, not a concrete vendor.
"""

import json
import os

PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")  # "anthropic" | "groq"


def call_structured(
    system_prompt: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict,
    user_content: str,
) -> dict:
    """
    Sends one structured (tool-forced) request and returns the raw dict
    matching input_schema -- regardless of which provider answered it.
    Callers (extraction.py, triage.py, answering.py) then validate that
    dict against their own Pydantic model, same as before.
    """
    if PROVIDER == "anthropic":
        return _call_anthropic(system_prompt, tool_name, tool_description, input_schema, user_content)
    elif PROVIDER == "groq":
        return _call_groq(system_prompt, tool_name, tool_description, input_schema, user_content)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {PROVIDER!r} (expected 'anthropic' or 'groq')")


def _call_anthropic(system_prompt, tool_name, tool_description, input_schema, user_content):
    import anthropic
    from config import ANTHROPIC_API_KEY

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1024,
        system=system_prompt,
        tools=[{"name": tool_name, "description": tool_description, "input_schema": input_schema}],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": user_content}],
    )
    tool_use_block = next(b for b in response.content if b.type == "tool_use")
    return tool_use_block.input


def _call_groq(system_prompt, tool_name, tool_description, input_schema, user_content):
    from openai import OpenAI  # Groq's API is OpenAI-compatible -- same SDK, different base_url

    client = OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=os.getenv("GROQ_API_KEY"),
    )
    response = client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        tools=[{
            "type": "function",
            "function": {"name": tool_name, "description": tool_description, "parameters": input_schema},
        }],
        tool_choice={"type": "function", "function": {"name": tool_name}},
    )
    tool_call = response.choices[0].message.tool_calls[0]
    return json.loads(tool_call.function.arguments)


def call_vision_text(image_base64: str, mime_type: str, prompt: str) -> str:
    """
    Provider-agnostic image -> text call. No tool schema here, just a
    plain text response -- the caller (extraction.py) is responsible
    for feeding the returned text into the normal text-based pipeline.
    Anthropic and OpenAI-compatible (Groq) APIs use different content
    block shapes for images, so both are handled here, same pattern as
    call_structured.
    """
    if PROVIDER == "anthropic":
        import anthropic
        from config import ANTHROPIC_API_KEY

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": image_base64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return response.content[0].text

    elif PROVIDER == "groq":
        from openai import OpenAI

        client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=os.getenv("GROQ_API_KEY"))
        response = client.chat.completions.create(
            model=os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b"),
            max_tokens=800,  # this model's free tier caps at 1000 output tokens/minute
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}},
                ],
            }],
        )
        return response.choices[0].message.content

    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {PROVIDER!r}")