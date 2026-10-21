"""
agent/triggers.py
Deterministic pre-checks for emergency conditions:
- Legal actions & regulatory complaints -> Legal & Compliance
- Account closure / deletion -> Account Operations
- Explicit human agent request -> General Support
- Fraud / unauthorized charges reports -> Security & Fraud
- Verification retries after a failed attempt (so the 3-strike lockout always counts)
All handoffs use templated summaries without raw user PII.
"""

from __future__ import annotations
import re
from typing import Any, Dict, List, Optional, Set
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


_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# "card 5555", "card is 0000", "last 4 4321", "ending in 4321"
_CARD_LAST4_PATTERN = re.compile(
    r"(?:\bcard\b|\blast\s*4\b|\bending\b)[^0-9]{0,25}(\d{4})\b",
    re.IGNORECASE,
)


def check_verification_retry(
    user_text: str,
    human_texts: List[str],
    session: SessionState | Dict[str, Any],
) -> Optional[Dict[str, str]]:
    """
    Deterministic backstop for the 3-strike lockout.

    The lockout lives in the service layer, but it only counts attempts that actually
    reach get_customer. If the model answers a retry such as "Try card 5555." with a
    clarifying question instead of calling the tool, the attempt is never counted.

    Once a customer has failed verification at least once, a new card last-4 therefore
    always produces a get_customer call built in code. Returns
    {"email": ..., "card_last4": ...}, or None when no forced call applies.
    First attempts are left to the model.

    human_texts: every human message so far, oldest first, including user_text.
    """
    handed_off = session.get("handed_off", False) if hasattr(session, "get") else session["handed_off"]
    verified = session.get("verified", False) if hasattr(session, "get") else session["verified"]
    failed_attempts = session.get("failed_attempts", 0) if hasattr(session, "get") else session["failed_attempts"]

    if handed_off or verified or failed_attempts < 1:
        return None

    card = _CARD_LAST4_PATTERN.search(user_text or "")
    if not card:
        return None

    # Reuse the most recent email the customer gave (earlier turn or this one)
    for text in reversed(human_texts):
        email = _EMAIL_PATTERN.search(text)
        if email:
            return {"email": email.group(0), "card_last4": card.group(1)}

    return None


def check_frozen_card_trigger(
    user_text: str,
    session: SessionState | Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    After a successful get_customer in the current turn, if the session's card
    status is 'frozen' and the user's latest message mentions card/frozen/unfreeze/blocked/declined,
    escalates directly to Security & Fraud (priority high).
    Does NOT escalate if the user verified for an unrelated request (e.g. a refund).
    """
    handed_off = session.get("handed_off", False) if hasattr(session, "get") else session["handed_off"]
    if handed_off:
        return None

    verified = session.get("verified", False) if hasattr(session, "get") else session["verified"]
    if not verified:
        return None

    card_status = session.get("card_status") if hasattr(session, "get") else session.get("card_status")
    if not card_status:
        db = session.get("db") if hasattr(session, "get") else session["db"]
        cust_id = session.get("customer_id") if hasattr(session, "get") else session["customer_id"]
        for c in db.get("customers", {}).values():
            if c.get("id") == cust_id:
                card_status = c.get("status")
                break

    if card_status != "frozen":
        return None

    text_lower = user_text.lower()

    # Do not escalate when a frozen customer verifies for an unrelated request (e.g. a refund)
    if re.search(r"\b(refund|refunds|chargeback|txn_|transaction)\b", text_lower):
        return None

    # Check if user's latest message mentions card/frozen/unfreeze/blocked/declined
    if not re.search(r"\b(card|frozen|unfreeze|blocked|declined)\b", text_lower):
        return None

    ticket = escalate_to_human(
        session,
        summary="Verified customer inquiry regarding frozen card status.",
        department="Security & Fraud",
        reason="FROZEN_CARD",
    )
    reply_msg = (
        "Your card shows as frozen. I cannot see why the card is frozen or unfreeze it from here. "
        f"I have escalated this to our Security & Fraud team under ticket {ticket['ticket_id']}. "
        "A team member will follow up with you on your ticket."
    )
    return {
        "trigger": "frozen_card",
        "ticket": ticket,
        "reply": reply_msg,
    }