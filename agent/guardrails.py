"""
agent/guardrails.py
Defines deterministic security layers for PII and Jailbreak detection.
"""

import re

# 1. Jailbreak & Prompt Injection Patterns (widened slightly, cheap backstop)
JAILBREAK_PATTERNS = [
    r"ignore\s+(all\s+)?(previous\s+)?instructions",
    r"you\s+are\s+now\s+in\s+dan\s+mode",
    r"system\s*:\s*override",
    r"disregard\s+(your\s+)?system\s+prompt",
    r"bypass\s+security\s+checks",
    r"pretend\s+you\s+are\s+an\s+unfiltered",
    r"forget\s+(all\s+)?(previous\s+)?rules",
    r"developer\s+mode\s+output",
    r"admin\s+override",
]


def check_input_guardrails(user_text: str) -> dict:
    """
    Evaluates raw user input for Prompt Injection / Jailbreaks.
    Off-topic keyword list removed: prompt handles conversational scope.
    """
    text_lower = user_text.lower()

    for pattern in JAILBREAK_PATTERNS:
        if re.search(pattern, text_lower):
            return {
                "safe": False,
                "reason": "Prompt injection attempt detected.",
                "action": "block",
                "message": "I am a Paylane support assistant and cannot process system-override commands.",
            }

    return {"safe": True}


def sanitize_output_pii(text: str) -> str:
    """
    Output Guardrail: Masks any email address and any card-like number (13-16 digits,
    with optional spaces/dashes) to [redacted], while preserving bare 4-digit references.
    """
    if not text:
        return ""
    # Mask email addresses
    sanitized = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        "[redacted]",
        text,
    )
    # Mask 13-16 digit card numbers (allowing spaces or dashes)
    sanitized = re.sub(r"\b(?:\d[ -]*?){13,16}\b", "[redacted]", sanitized)
    return sanitized