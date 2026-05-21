# model_server/summarizer.py
import os
from groq import Groq

_client: Groq | None = None

def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _client

def summarize_thread(text: str) -> str:
    prompt = (
        "You are a maintainer triaging issues. Summarize the following issue/thread in exactly 3 lines: "
        "Line 1: WHAT is reported. Line 2: ASK or expected behavior. Line 3: STATE or current impact. "
        "Be concise, max 80 words total.\n\n"
        f"{text[:8000]}"   # truncate to 8k chars
    )
    response = _get_client().chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=150,
    )
    return response.choices[0].message.content.strip()