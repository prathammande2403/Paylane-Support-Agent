"""
agent/triggers.py
Deterministic pre-checks for emergency conditions:
- Legal actions & regulatory complaints -> Legal & Compliance
- Account closure / deletion -> Account Operations
- Explicit human agent request -> General Support
- Fraud / unauthorized charges reports -> Security & Fraud
All handoffs use templated summaries without raw user PII.
"""

from __future__ import annotations
import re
from typing import Any, Dict, Optional, Set
from paylane_api.service import SessionState, escalate_to_human


def parse_reported_dollar_amounts(text: str) -> Optional[float]:
    """
    Parses dollar amounts ONLY from tokens with '$', 'USD' or 'dollars'.
    Ignores plain digits (like card numbers 4242 or txn IDs).
    Returns sum of distinct amounts, or None if no dollar amounts found.
    """
    amounts: Set[float] = set()

    # Pattern 1: $123 or $123.45
    for m in re.finditer(r"\$\s*(\d+(?:\.\d{1,2})?)", text):
        try:
            amounts.add(float(m.group(1)))
        except ValueError:
            pass

    # Pattern 2: 123 USD or 123 dollars
    for m in re.finditer(r"\b(\d+(?:\.\d{1,2})?)\s*(?:usd|dollars?)\b", text, re.IGNORECASE):
        try:
            amounts.add(float(m.group(1)))
        except ValueError:
            pass

    if not amounts:
        return None

    return sum(amounts)


def check_deterministic_triggers(
    user_text: str,
    session: SessionState | Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Evaluates new human message for immediate deterministic handoff triggers.
    If session is already handed off, skips triggers to avoid duplicate tickets.
    Returns dict with trigger details and templated reply, or None if no trigger fired.
    """
    handed_off = session.get("handed_off", False) if hasattr(session, "get") else session["handed_off"]
    if handed_off:
        return None

    text_lower = user_text.lower()
    verified = session.get("verified", False) if hasattr(session, "get") else session["verified"]

    # 1. Legal action / regulatory complaint
    legal_pattern = r"\b(lawyer|attorney|lawsuit|legal action|regulator|cfpb|ombudsman|court|solicitor)\b"
    if re.search(legal_pattern, text_lower) or "sue paylane" in text_lower or re.search(r"\bsue\b", text_lower):
        summary = f"Customer reported legal action or regulatory complaint. Verified: {verified}."
        ticket = escalate_to_human(
            session,
            summary=summary,
            department="Legal & Compliance",
            reason="LEGAL",
        )
        reply = (
            f"I have escalated your legal inquiry to our Legal & Compliance team. "
            f"Your ticket ID is {ticket['ticket_id']}. A team member will follow up on your case."
        )
        return {"trigger": "legal", "ticket": ticket, "reply": reply}

    # 2. Account closure / deletion
    closure_pattern = (
        r"\b(close|delete|cancel|deactivate)\b.{0,30}\baccount\b"
        r"|\bclose the account\b"
        r"|\bcancel the account\b"
    )
    if re.search(closure_pattern, text_lower):
        summary = f"Customer requested account closure or deletion. Verified: {verified}."
        ticket = escalate_to_human(
            session,
            summary=summary,
            department="Account Operations",
            reason="ACCOUNT_CLOSURE",
        )
        reply = (
            f"I have connected your account closure request to our Account Operations team. "
            f"Your ticket ID is {ticket['ticket_id']}. A team member will follow up to assist you."
        )
        return {"trigger": "account_closure", "ticket": ticket, "reply": reply}

    # 3. Explicit human request
    human_pattern = (
        r"\b(human|real person|representative|speak to (someone|a human|an agent)|"
        r"talk to (someone|a human|an agent)|connect me to (a human|an agent)|"
        r"human support|human representative)\b"
    )
    if re.search(human_pattern, text_lower) or "dont want to talk to an ai" in text_lower or "don't want to talk to an ai" in text_lower:
        summary = f"Customer explicitly requested human representative. Verified: {verified}."
        ticket = escalate_to_human(
            session,
            summary=summary,
            department="General Support",
            reason="EXPLICIT_HUMAN",
        )
        reply = (
            f"I have connected you with our human support team. "
            f"Your ticket ID is {ticket['ticket_id']}. A team member will follow up to assist you."
        )
        return {"trigger": "explicit_human", "ticket": ticket, "reply": reply}

    # 4. Fraud / unauthorized charge report
    generic_question = (
        re.search(r"\bhow (do|does|can) (you|paylane) (prevent|handle|detect|stop) fraud\b", text_lower)
        or re.search(r"\bwhat is (your|the) fraud policy\b", text_lower)
        or "how do you prevent fraud" in text_lower
    )

    if not generic_question:
        fraud_patterns = [
            r"\bunauthorized\b",
            r"\bdid\s*n['o]t\s*make\b",
            r"\bstolen\b",
            r"\bhacked\b",
            r"\bsomeone\s+used\s+my\s+card\b",
            r"\bfraudulent\s+charge\b",
            r"\bfraud\s+on\s+my\b",
            r"\bcompromised\b",
        ]
        if any(re.search(p, text_lower) for p in fraud_patterns):
            loss_usd = parse_reported_dollar_amounts(user_text)
            loss_str = f"${loss_usd:.2f}" if loss_usd is not None else "unspecified"
            summary = f"Customer reported fraud/unauthorized charge. Loss: {loss_str}. Verified: {verified}."
            ticket = escalate_to_human(
                session,
                summary=summary,
                department="Security & Fraud",
                reported_loss_usd=loss_usd,
                reason="FRAUD_REPORT",
            )
            reply = (
                f"Your fraud report has been escalated to our Security & Fraud team under ticket "
                f"{ticket['ticket_id']}. A specialist will follow up with you."
            )
            return {"trigger": "fraud", "ticket": ticket, "reply": reply}

    return None
