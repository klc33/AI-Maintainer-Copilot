# app/infra/tracing.py
import os
import langfuse

def get_langfuse_client():
    """Return a Langfuse client configured for our self‑hosted instance."""
    return langfuse.Langfuse(
        public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", "pk-lf-dev"),
        secret_key=os.environ.get("LANGFUSE_SECRET_KEY", "sk-lf-dev"),
        host=os.environ.get("LANGFUSE_HOST", "http://langfuse:3000"),
    )