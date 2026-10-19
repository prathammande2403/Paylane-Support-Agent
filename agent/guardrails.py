"""
agent/guardrails.py
Defines deterministic security layers for PII, Jailbreaks, Prompt Injections, and Topic Boundaries.
"""

import re

# 1. Jailbreak & Prompt Injection Patterns
JAILBREAK_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"you\s+are\s+now\s+in\s+DAN\s+mode",
    r"system\s*:\s*override",
    r"disregard\s+(your\s+)?system\s+prompt",
    r"bypass\s+security\s+checks",
    r"pretend\s+you\s+are\s+an\s+unfiltered"
]

# 2. Sensitive / Banned Topics (Off-Topic or Dangerous Content)
OFF_TOPIC_KEYWORDS = [
    "crypto investment", "bitcoin prediction", "write python code for",
    "tell me a joke", "recipe for", "political election"
]

def check_input_guardrails(user_text: str) -> dict:
    """
    Evaluates raw user input for Prompt Injection, Jailbreaks, and Off-Topic chatter.
    """
    text_lower = user_text.lower()

    # Check Jailbreak / Prompt Injection
    for pattern in JAILBREAK_PATTERNS:
        if re.search(pattern, text_lower):
            return {
                "safe": False,
                "reason": "Prompt injection attempt detected.",
                "action": "block",
                "message": "I am a Paylane support assistant and cannot process system-override commands."
            }

    # Check Off-Topic Requests
    for keyword in OFF_TOPIC_KEYWORDS:
        if keyword in text_lower:
            return {
                "safe": False,
                "reason": "Off-topic query detected.",
                "action": "refuse",
                "message": "I can only assist with Paylane account services, card management, and transaction refunds."
            }

    return {"safe": True}

def sanitize_output_pii(text: str) -> str:
    """
    Output Guardrail: Redacts any accidentally generated full Credit Card Numbers or Social Security Numbers.
    """
    # Regex for standard 16-digit credit card patterns
    card_pattern = r"\b(?:\d[ -]*?){13,16}\b"
    # Redact full card numbers except the last 4
    sanitized = re.sub(card_pattern, "****-****-****-XXXX", text)
    return sanitized