"""
agent/config.py
Loads environment variables and initializes LangSmith tracing configuration.
"""

import os
import sys
from dotenv import load_dotenv

# Ensure UTF-8 output encoding if supported by stdout to avoid Windows cp1252 errors
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Load environment variables from .env file
load_dotenv()

# Verify required keys - read GROQ_API_KEY from env only, never hardcode
_groq_key = os.getenv("GROQ_API_KEY")
if not _groq_key:
    raise ValueError("CRITICAL: GROQ_API_KEY is missing from environment variables or .env file.")

# Exported configuration values
GROQ_API_KEY: str = _groq_key
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# LangSmith telemetry status message
if os.getenv("LANGCHAIN_TRACING_V2") == "true" and os.getenv("LANGCHAIN_API_KEY"):
    print("[LangSmith] Tracing enabled successfully for project:", os.getenv("LANGCHAIN_PROJECT", "paylane-support-agent"))
else:
    print("[LangSmith] Tracing disabled or LangChain API key not provided. Running locally.")