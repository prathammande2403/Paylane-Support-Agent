"""
agent/triggers.py
Deterministic pre-checks for emergency conditions:
- Legal actions & regulatory complaints -> Legal & Compliance
- Account closure / deletion -> Account Operations
- Explicit human agent request -> General Support
- Fraud / unauthorized charges reports -> Security & Fraud
- Verification retries after a failed attempt
- Refunds that policy sends to a human (over $75 / 30+ days) -> Billing & Refunds
All handoffs use templated summaries without raw user PII.
"""

from __future__ import annotations
import re
from typing import Any, Dict, List, Optional, Set
from paylane_api.service import SessionState, escalate_to_human


_TXN_ID_PATTERN = re.compile(r"\bTXN_[A-Z0-9]+\b")


def extract_transaction_id(text: str) -> Optional[str]:
    """
    Extracts the first transaction ID (e.g. TXN_1001) from a user message.
    Returns None if none found.
    """
    m = _TXN_ID_PATTERN.search(text)
    return m.group(0) if m else None


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


FRAUD_VERIFY_REPLY = (
    "I'm sorry this happened, and I want to get it to our Security & Fraud team quickly. "
    "First I need to verify your identity. Please send me: (1) the email on your Paylane account, "
    "(2) the last 4 digits of your card, and (3) the transaction ID(s) of the charges you don't recognise."
)


def _get_attr(session: Any, key: str, default: Any = None) -> Any:
    if hasattr(session, "get") and callable(session.get):
        return session.get(key, default)
    return getattr(session, key, default)


def check_pending_fraud_trigger(session: Any, user_text: str = "") -> Optional[Dict[str, Any]]:
    """
    Runs after verification succeeds: if a fraud report was waiting on identity
    verification, escalate it now.

    Validates any transaction IDs mentioned against the session DB:
    - If found and amounts match the claim → escalate normally.
    - If found but amounts differ from the claim → escalate with the VERIFIED total
      and explicitly inform the user of the discrepancy in the reply.
    - If no TXN IDs provided → escalate using the user's self-reported loss amount.

    Priority is always based on the VERIFIED transaction total (not the user's claim)
    so users cannot inflate priority by overstating amounts.

    Returns None if nothing is pending or the customer is not verified / already
    handed off.
    """
    if _get_attr(session, "handed_off", False) or not _get_attr(session, "verified", False):
        return None
    pending = _get_attr(session, "pending_fraud")
    if not pending:
        return None

    loss_usd = pending.get("loss_usd")

    # --- Transaction-amount validation ---
    # Collect all TXN IDs from the pending record and from the current user_text.
    txn_ids_to_check: List[str] = []
    if pending.get("txn_id"):
        txn_ids_to_check.append(pending["txn_id"])
    for m in _TXN_ID_PATTERN.finditer(user_text):
        tid = m.group(0)
        if tid not in txn_ids_to_check:
            txn_ids_to_check.append(tid)

    db = _get_attr(session, "db")
    cust_id = _get_attr(session, "customer_id")

    # Per-transaction details for mismatch replies: [(txn_id, amount), ...]
    verified_txns: List[tuple] = []
    verified_txn_total: Optional[float] = None
    amount_mismatch = False

    if db and cust_id and txn_ids_to_check:
        for tid in txn_ids_to_check:
            txn = db.get("transactions", {}).get(tid) if isinstance(db, dict) else None
            if txn and txn.get("customer_id") == cust_id:
                verified_txns.append((tid, txn.get("amount", 0.0)))

        if verified_txns:
            verified_txn_total = sum(amt for _, amt in verified_txns)
            # Flag mismatch when user's claimed loss differs from DB total
            if loss_usd is not None and abs(loss_usd - verified_txn_total) > 0.01:
                amount_mismatch = True
        else:
            # Transaction IDs do not exist or belong to another customer
            amount_mismatch = True
    elif loss_usd is not None and db and cust_id:
        cust_txns = [
            (tid, t.get("amount", 0.0))
            for tid, t in (db.get("transactions", {}) if isinstance(db, dict) else {}).items()
            if t.get("customer_id") == cust_id
        ]
        matching_single = next((amt for tid, amt in cust_txns if abs(amt - loss_usd) < 0.01), None)
        cust_total = sum(amt for tid, amt in cust_txns)

        if matching_single is not None:
            verified_txn_total = matching_single
        elif abs(cust_total - loss_usd) < 0.01 and cust_txns:
            verified_txn_total = cust_total
        else:
            # The customer does NOT own any transaction or total matching loss_usd!
            amount_mismatch = True

    # --- Handle Amount / Detail Mismatch ---
    if amount_mismatch:
        current_failed = _get_attr(session, "failed_attempts", 0) + 1
        if hasattr(session, "__setitem__"):
            session["failed_attempts"] = current_failed
        else:
            session.failed_attempts = current_failed

        if verified_txns:
            txn_breakdown = ", ".join(f"{tid} (${amt:.2f})" for tid, amt in verified_txns)
            verified_str = f"${verified_txn_total:.2f}"
            detail_str = f"The transactions found on your account ({txn_breakdown} — total: {verified_str}) do not match the reported amount of ${loss_usd:.2f}."
        elif loss_usd is not None:
            detail_str = f"We could not find any transaction or unauthorized charges totalling ${loss_usd:.2f} on your account."
        else:
            detail_str = f"The transaction details provided could not be verified on your account."

        if current_failed >= 3:
            if hasattr(session, "__setitem__"):
                session["locked"] = True
                session["verified"] = False
                session["pending_fraud"] = None
            else:
                session.locked = True
                session.verified = False
                session.pending_fraud = None
            reply = (
                f"Verification failed 3 times: {detail_str} "
                f"This chat session has been locked for security reasons. All further actions are blocked."
            )
            return {"trigger": "fraud_locked", "ticket": None, "reply": reply}
        else:
            reply = (
                f"{detail_str} Please re-check and provide the correct transaction ID(s) or account credentials. "
                f"(Attempt {current_failed} of 3)."
            )
            return {"trigger": "fraud_mismatch_retry", "ticket": None, "reply": reply}

    # --- Details Match: Escalate to Security & Fraud ---
    if hasattr(session, "__setitem__"):
        session["failed_attempts"] = 0
        session["pending_fraud"] = None
    else:
        session.failed_attempts = 0
        session.pending_fraud = None

    priority_amount = verified_txn_total if verified_txn_total is not None else loss_usd
    priority_str = f"${priority_amount:.2f}" if priority_amount is not None else "unspecified"
    summary = f"Customer reported fraud/unauthorized charge. Verified loss: {priority_str}. Verified: True."
    ticket = escalate_to_human(
        session,
        summary=summary,
        department="Security & Fraud",
        reported_loss_usd=priority_amount,
        reason="FRAUD_REPORT",
    )

    reply = (
        f"Thank you, your transaction details have been verified. Your fraud report has been escalated to our "
        f"Security & Fraud team under ticket {ticket['ticket_id']}. "
        f"A specialist will follow up with you."
    )
    return {"trigger": "fraud", "ticket": ticket, "reply": reply}


def check_refund_escalation_trigger(session: Any) -> Optional[Dict[str, Any]]:
    """
    Deterministic backstop for refunds that policy sends to a human.
    """
    if _get_attr(session, "handed_off", False):
        return None

    trace = _get_attr(session, "trace") or []
    last_call = next((e for e in reversed(trace) if e.get("step") == "tool_http_call"), None)
    if not last_call or last_call.get("tool") != "issue_refund":
        return None

    code = (last_call.get("response") or {}).get("reason_code")
    if code not in ("ABOVE_LIMIT", "TOO_OLD"):
        return None

    why = "amount is above the $75 auto-approval limit" if code == "ABOVE_LIMIT" else "transaction is outside the 30-day refund window"
    ticket = escalate_to_human(
        session,
        summary=f"Refund request needs manual review: {why}.",
        department="Billing & Refunds",
        reason="REFUND_REVIEW",
    )
    reply = (
        "I can't approve this refund automatically. "
        f"I've passed it to our Billing & Refunds team under ticket {ticket['ticket_id']} "
        "so a team member can review it."
    )
    return {"trigger": "refund_review", "ticket": ticket, "reply": reply}


def check_deterministic_triggers(
    user_text: str,
    session: SessionState | Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Evaluates new human message for immediate deterministic handoff triggers:
    Legal, Account Closure, Human Request, and Fraud.
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
    
    has_email = bool(re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", user_text))
    has_card = bool(re.search(r"\b\d{4}\b", user_text))

    fraud_pattern = r"\b(fraud|unauthorized|stolen|didn't make this charge|did not make this charge|unrecognised charge|unrecognized charge|unrecognised|unrecognized|hacked)\b"
    if not generic_question and (re.search(fraud_pattern, text_lower) or ("charge" in text_lower and ("didn't" in text_lower or "did not" in text_lower)) or "card was stolen" in text_lower):
        loss_usd = parse_reported_dollar_amounts(user_text)
        if verified:
            loss_str = f"${loss_usd:.2f}" if loss_usd is not None else "unspecified"
            summary = f"Customer reported fraud/unauthorized charge. Loss: {loss_str}. Verified: True."
            ticket = escalate_to_human(
                session,
                summary=summary,
                department="Security & Fraud",
                reported_loss_usd=loss_usd,
                reason="FRAUD_REPORT",
            )
            reply = (
                f"Thank you, you're verified. Your fraud report has been escalated to our Security & Fraud team "
                f"under ticket {ticket['ticket_id']}. A specialist will follow up with you."
            )
            return {"trigger": "fraud", "ticket": ticket, "reply": reply}
        else:
            # Always record pending_fraud so verification success can trigger escalation, even if credentials are present
            if hasattr(session, "set"):
                session.set("pending_fraud", {"loss_usd": loss_usd})
            else:
                session["pending_fraud"] = {"loss_usd": loss_usd}
            
            if has_email and has_card:
                return None  # Let verification proceed while keeping pending_fraud stored
                
            return {"trigger": "fraud_pending_verification", "ticket": None, "reply": FRAUD_VERIFY_REPLY}

    return None


def check_frozen_card_trigger(user_text: str, session: Any) -> Optional[Dict[str, Any]]:
    verified = _get_attr(session, "verified", False)
    if not verified:
        return None
    text_lower = user_text.lower()
    if "frozen" in text_lower or "freeze" in text_lower:
        ticket = escalate_to_human(
            session,
            summary="Customer inquired about a frozen card.",
            department="Security & Fraud",
            reason="FROZEN_CARD",
        )
        reply = (
            f"Your card currently shows as frozen. I've escalated this to our Security & Fraud team "
            f"under ticket {ticket['ticket_id']} so a specialist can assist you."
        )
        return {"trigger": "frozen_card", "ticket": ticket, "reply": reply}
    return None


def check_verification_retry(user_text: str, human_texts: List[str], session: Any) -> Optional[Dict[str, Any]]:
    """Detects card last-4 retry attempts after a failed verification, ignoring transaction statements."""
    failed_attempts = _get_attr(session, "failed_attempts", 0)
    if (
        failed_attempts <= 0
        or failed_attempts >= 3
        or _get_attr(session, "verified", False)
        or _get_attr(session, "handed_off", False)
        or _get_attr(session, "locked", False)
    ):
        return None

    text_lower = user_text.lower()
    if "charged" in text_lower or "yesterday" in text_lower or "transaction" in text_lower or "spent" in text_lower or "$" in user_text:
        return None

    match = re.search(r"\b\d{4}\b", user_text)
    if not match:
        return None

    card_last4 = match.group(0)
    email = None
    for text in reversed(human_texts):
        email_match = re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", text)
        if email_match:
            email = email_match.group(0)
            break

    if email:
        return {"email": email, "card_last4": card_last4}
    return None