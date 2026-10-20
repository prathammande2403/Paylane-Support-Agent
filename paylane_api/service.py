"""
paylane_api/service.py
Pure-Python business logic layer and single source of truth for all Paylane policy rules.
Operates on a per-session object:
{session_id, db, verified, customer_id, failed_attempts, handed_off, ticket, trace}.
No LLM or FastAPI dependencies.
"""

from __future__ import annotations
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from data.mock_db import FIXED_TEST_CLOCK, get_fresh_db

# Allowed departments for escalations
ALLOWED_DEPARTMENTS = {
    "Billing & Refunds",
    "Security & Fraud",
    "Legal & Compliance",
    "Account Operations",
    "General Support",
}

# Deterministic counters
_ticket_counter: int = 0
_refund_counter: int = 0


def reset_counters():
    """Resets deterministic ticket and refund counters."""
    global _ticket_counter, _refund_counter
    _ticket_counter = 0
    _refund_counter = 0


def get_next_ticket_id() -> str:
    """Returns next deterministic ticket ID (e.g. TCK-0001)."""
    global _ticket_counter
    _ticket_counter += 1
    return f"TCK-{_ticket_counter:04d}"


def get_next_refund_id() -> str:
    """Returns next deterministic refund ID (e.g. REF-0001)."""
    global _refund_counter
    _refund_counter += 1
    return f"REF-{_refund_counter:04d}"


def strip_pii_from_text(text: str) -> str:
    """
    Strips email addresses and 13-16 digit card sequences from text.
    Keeps 4-digit last4 mentions intact.
    """
    if not text:
        return ""
    # Strip emails
    sanitized = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "[redacted]",
        text,
    )
    # Strip 13-16 digit sequences
    sanitized = re.sub(r"\b(?:\d[ -]*?){13,16}\b", "[redacted]", sanitized)
    return sanitized


class SessionState:
    """Encapsulates isolated per-session server-side state."""

    def __init__(
        self,
        session_id: str,
        db: Optional[Dict[str, Any]] = None,
        verified: bool = False,
        customer_id: Optional[str] = None,
        failed_attempts: int = 0,
        handed_off: bool = False,
        ticket: Optional[Dict[str, Any]] = None,
        trace: Optional[List[Dict[str, Any]]] = None,
        card_status: Optional[str] = None,
    ):
        self.session_id = session_id
        self.db = db if db is not None else get_fresh_db()
        self.verified = verified
        self.customer_id = customer_id
        self.failed_attempts = failed_attempts
        self.handed_off = handed_off
        self.ticket = ticket
        self.trace = trace if trace is not None else []
        self.card_status = card_status

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any):
        setattr(self, key, value)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "verified": self.verified,
            "customer_id": self.customer_id,
            "failed_attempts": self.failed_attempts,
            "handed_off": self.handed_off,
            "ticket": self.ticket,
            "trace": self.trace,
            "card_status": self.card_status,
        }


def verify_customer(
    session: SessionState | Dict[str, Any],
    email: str,
    card_last4: str,
) -> Dict[str, Any]:
    """
    Verifies customer identity against session DB.
    Guards:
    - If session is already handed off/locked: returns LOCKED without evaluating.
    - Constant mismatch message regardless of whether email or last4 was wrong.
    - 3rd consecutive failure creates Account Operations ticket and locks session.
    - On success: marks verified ONLY in the session, never mutates mock DB.
    - Never reveals email or full card; returns {ok, name, card_status}.
    """
    handed_off = session.get("handed_off", False) if hasattr(session, "get") else session["handed_off"]
    if handed_off:
        return {
            "ok": False,
            "reason": "Account is locked or already handed off to human support. No account actions permitted.",
            "reason_code": "LOCKED",
        }

    clean_email = email.lower().strip() if email else ""
    clean_last4 = str(card_last4).strip() if card_last4 else ""

    db = session.get("db") if hasattr(session, "get") else session["db"]
    customer = db["customers"].get(clean_email)

    # Check match: both email exists and card_last4 matches
    if not customer or customer.get("card_last4") != clean_last4:
        current_failed = (session.get("failed_attempts", 0) if hasattr(session, "get") else session["failed_attempts"]) + 1
        if hasattr(session, "__setitem__"):
            session["failed_attempts"] = current_failed
        else:
            session.failed_attempts = current_failed

        if current_failed >= 3:
            ticket = escalate_to_human(
                session,
                summary="Three consecutive failed verification attempts. Account locked.",
                department="Account Operations",
                reason="THREE_STRIKE_LOCKOUT",
            )
            return {
                "ok": False,
                "reason": (
                    f"Verification failed 3 times. Account actions have been locked and the case "
                    f"is escalated to Account Operations under ticket {ticket['ticket_id']}."
                ),
                "reason_code": "LOCKED",
                "failed_attempts": current_failed,
                "ticket_id": ticket["ticket_id"],
            }

        return {
            "ok": False,
            "reason": f"Verification failed: Email or card last 4 digits do not match our records. (Attempt {current_failed} of 3).",
            "reason_code": "MISMATCH",
            "failed_attempts": current_failed,
        }

    # Successful verification - record in session ONLY
    if hasattr(session, "__setitem__"):
        session["verified"] = True
        session["customer_id"] = customer["id"]
        session["card_status"] = customer["status"]
        session["failed_attempts"] = 0
    else:
        session.verified = True
        session.customer_id = customer["id"]
        session.card_status = customer["status"]
        session.failed_attempts = 0

    return {
        "ok": True,
        "id": customer["id"],
        "name": customer["name"],
        "card_status": customer["status"],
        "status": customer["status"],
        "card_last4": customer["card_last4"],
    }


def get_transaction(
    session: SessionState | Dict[str, Any],
    txn_id: str,
) -> Dict[str, Any]:
    """
    Retrieves transaction details.
    Guard order:
    1. handed_off -> blocked
    2. not verified -> blocked
    3. not found / not owned (same response for both: NOT_FOUND)
    4. return transaction details
    """
    handed_off = session.get("handed_off", False) if hasattr(session, "get") else session["handed_off"]
    if handed_off:
        return {
            "ok": False,
            "reason": "Session has been handed off to human support. Account actions are suspended.",
            "reason_code": "HANDED_OFF",
        }

    verified = session.get("verified", False) if hasattr(session, "get") else session["verified"]
    if not verified:
        return {
            "ok": False,
            "reason": "Customer identity must be verified before retrieving transaction details.",
            "reason_code": "UNVERIFIED",
        }

    cust_id = session.get("customer_id") if hasattr(session, "get") else session["customer_id"]
    db = session.get("db") if hasattr(session, "get") else session["db"]
    txn = db["transactions"].get(txn_id)

    if not txn or txn.get("customer_id") != cust_id:
        return {
            "ok": False,
            "reason": "Transaction ID not found.",
            "reason_code": "NOT_FOUND",
        }

    return {
        "ok": True,
        "txn_id": txn["txn_id"],
        "customer_id": txn["customer_id"],
        "amount": txn["amount"],
        "date": txn["date"],
        "merchant": txn["merchant"],
        "status": txn["status"],
    }


def issue_refund(
    session: SessionState | Dict[str, Any],
    txn_id: str,
    amount: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Issues a refund enforcing exact Paylane policy rules.
    Guard order:
    1. handed_off
    2. not verified
    3. not found / not owned (NOT_FOUND)
    4. already refunded (ALREADY_REFUNDED)
    5. pending or other non-settled status (PENDING / NOT_SETTLED)
    6. future-dated (FUTURE_DATED)
    7. partial/altered amount (PARTIAL_NOT_ALLOWED)
    8. amount > 75.00 (ABOVE_LIMIT)
    9. elapsed time >= 30 days (TOO_OLD)
    10. mark refunded in session DB, generate deterministic refund_id, return ok:true
    """
    handed_off = session.get("handed_off", False) if hasattr(session, "get") else session["handed_off"]
    if handed_off:
        return {
            "ok": False,
            "reason": "Session has been handed off to human support. Consequential actions are suspended.",
            "reason_code": "HANDED_OFF",
        }

    verified = session.get("verified", False) if hasattr(session, "get") else session["verified"]
    if not verified:
        return {
            "ok": False,
            "reason": "Customer identity must be verified before requesting a refund.",
            "reason_code": "UNVERIFIED",
        }

    cust_id = session.get("customer_id") if hasattr(session, "get") else session["customer_id"]
    db = session.get("db") if hasattr(session, "get") else session["db"]
    txn = db["transactions"].get(txn_id)

    if not txn or txn.get("customer_id") != cust_id:
        return {
            "ok": False,
            "reason": "Transaction ID not found.",
            "reason_code": "NOT_FOUND",
        }

    if txn.get("status") == "refunded":
        return {
            "ok": False,
            "reason": "This transaction has already been refunded.",
            "reason_code": "ALREADY_REFUNDED",
        }

    if txn.get("status") != "settled":
        code = "PENDING" if txn.get("status") == "pending" else "NOT_SETTLED"
        return {
            "ok": False,
            "reason": f"Cannot refund transaction with status '{txn.get('status')}'. Pending charges cannot be refunded until settled.",
            "reason_code": code,
        }

    # Date parsing and timezone awareness
    txn_date = datetime.fromisoformat(txn["date"])
    if txn_date.tzinfo is None:
        txn_date = txn_date.replace(tzinfo=timezone.utc)

    if txn_date > FIXED_TEST_CLOCK:
        return {
            "ok": False,
            "reason": "Transaction date is in the future. Cannot refund.",
            "reason_code": "FUTURE_DATED",
        }

    # Partial / altered amount check
    if amount is not None:
        if amount <= 0 or abs(amount - txn["amount"]) > 1e-4:
            return {
                "ok": False,
                "reason": "Partial or altered refunds are not permitted. Only the full original transaction amount may be refunded.",
                "reason_code": "PARTIAL_NOT_ALLOWED",
            }

    # Amount auto-approval limit ($75.00 auto-approved, $75.01 escalates)
    if txn["amount"] > 75.00:
        return {
            "ok": False,
            "reason": f"Refund amount (${txn['amount']:.2f}) exceeds $75 auto-approval limit. Must escalate to Billing & Refunds.",
            "reason_code": "ABOVE_LIMIT",
        }

    # Elapsed time (< 30 days auto-approved, exactly 30 days escalates)
    elapsed = FIXED_TEST_CLOCK - txn_date
    if elapsed >= timedelta(days=30):
        return {
            "ok": False,
            "reason": f"Transaction is {elapsed.days} days old. Exceeds 30-day refund window. Must escalate to Billing & Refunds.",
            "reason_code": "TOO_OLD",
        }

    # Auto-approve and mark refunded in session DB
    txn["status"] = "refunded"
    refund_id = get_next_refund_id()
    db.setdefault("refunds", {})[refund_id] = {
        "refund_id": refund_id,
        "txn_id": txn_id,
        "amount": txn["amount"],
        "created_at": FIXED_TEST_CLOCK.isoformat(),
    }

    return {
        "ok": True,
        "refund_id": refund_id,
        "amount": txn["amount"],
        "message": f"Refund successfully issued. Refund ID: {refund_id}",
    }


def escalate_to_human(
    session: SessionState | Dict[str, Any],
    summary: str,
    department: str,
    reported_loss_usd: Optional[float] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Escalates an issue to human support.
    - Validates department against allowed set.
    - Computes priority in code:
      - 'high' if reported_loss_usd > 500, or for 3-strike lockout, or frozen card.
      - else 'normal'.
    - If session already has an open ticket, returns existing ticket.
    - Deterministic ticket ID (TCK-0001).
    - Strips emails and card numbers from summary.
    - Sets handed_off = True in session.
    """
    if department not in ALLOWED_DEPARTMENTS:
        raise ValueError(
            f"Invalid department '{department}'. Must be one of {sorted(ALLOWED_DEPARTMENTS)}"
        )

    # Return existing ticket if already open
    existing_ticket = session.get("ticket") if hasattr(session, "get") else session.get("ticket")
    if existing_ticket:
        if hasattr(session, "__setitem__"):
            session["handed_off"] = True
        else:
            session.handed_off = True
        return existing_ticket

    # Compute priority in code
    summary_lower = (summary or "").lower()
    is_frozen_card = (
        reason == "FROZEN_CARD"
        or "frozen" in summary_lower
        or (department == "Security & Fraud" and "frozen" in summary_lower)
    )
    is_lockout = reason == "THREE_STRIKE_LOCKOUT" or "locked" in summary_lower

    if reported_loss_usd is not None and reported_loss_usd > 500:
        priority = "high"
    elif is_lockout or is_frozen_card:
        priority = "high"
    else:
        priority = "normal"

    sanitized_summary = strip_pii_from_text(summary)
    ticket_id = get_next_ticket_id()
    cust_id = session.get("customer_id") if hasattr(session, "get") else session.get("customer_id")

    ticket_data = {
        "ticket_id": ticket_id,
        "customer_id": cust_id or "GUEST",
        "department": department,
        "priority": priority,
        "summary": sanitized_summary,
        "status": "open",
        "created_at": FIXED_TEST_CLOCK.isoformat(),
        "reported_loss_usd": reported_loss_usd,
        "reason": reason,
    }

    if hasattr(session, "__setitem__"):
        session["ticket"] = ticket_data
        session["handed_off"] = True
    else:
        session.ticket = ticket_data
        session.handed_off = True

    db = session.get("db") if hasattr(session, "get") else session.get("db")
    if db is not None:
        db.setdefault("tickets", {})[ticket_id] = ticket_data

    return ticket_data
