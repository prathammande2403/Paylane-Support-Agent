"""
tests/test_regressions.py
Regression tests reproducing the 16 known bugs listed in Section 1.
These tests MUST PASS after the Phase 1+ refactor, proving all bugs are fixed.
The baseline_failures.txt file captures these same tests failing on the original code.
Tests use the pure service layer directly (no LLM, fast).
"""

from datetime import timedelta
import pytest
from langchain_core.messages import HumanMessage

from data.mock_db import FIXED_TEST_CLOCK, get_fresh_db
from paylane_api.service import (
    SessionState,
    verify_customer,
    get_transaction,
    issue_refund,
    escalate_to_human,
    reset_counters,
)
from agent.guardrails import sanitize_output_pii
from agent.tools import get_customer, get_transaction as tool_get_transaction, issue_refund as tool_issue_refund, escalate_to_human as tool_escalate_to_human


@pytest.fixture(autouse=True)
def clean_state():
    reset_counters()


def make_session(verified=False, customer_id=None, handed_off=False, failed_attempts=0):
    s = SessionState(session_id="test-reg", db=get_fresh_db())
    s.verified = verified
    s.customer_id = customer_id
    s.handed_off = handed_off
    s.failed_attempts = failed_attempts
    return s


# --- Bug 1: Unverified users cannot read or refund ---
def test_bug1_unverified_user_cannot_read_transaction():
    """Unverified user must not read any transaction."""
    session = make_session()
    res = get_transaction(session, "TXN_1001")
    assert res["ok"] is False
    assert res["reason_code"] == "UNVERIFIED"


def test_bug1_unverified_user_cannot_refund_transaction():
    """Unverified user must not refund any transaction."""
    session = make_session()
    res = issue_refund(session, "TXN_1001")
    assert res["ok"] is False
    assert res["reason_code"] == "UNVERIFIED"


# --- Bug 2: customer_id must not be in tool model-visible signatures ---
def test_bug2_customer_id_not_in_tool_get_transaction_signature():
    """customer_id must not be in get_transaction model-visible args."""
    assert "customer_id" not in tool_get_transaction.args


def test_bug2_customer_id_not_in_tool_issue_refund_signature():
    """customer_id must not be in issue_refund model-visible args."""
    assert "customer_id" not in tool_issue_refund.args


def test_bug2_customer_id_not_in_tool_escalate_signature():
    """customer_id must not be in escalate_to_human model-visible args."""
    assert "customer_id" not in tool_escalate_to_human.args


# --- Bug 3: 3-strike lockout works and blocks subsequent correct credentials ---
def test_bug3_three_strikes_locks_account():
    """After 3 bad attempts, session is locked and correct credentials are rejected."""
    session = make_session()
    verify_customer(session, "user1@paylane.com", "0000")  # attempt 1
    verify_customer(session, "user1@paylane.com", "0000")  # attempt 2
    res3 = verify_customer(session, "user1@paylane.com", "0000")  # attempt 3 - locks
    assert res3["reason_code"] == "LOCKED"
    assert session.handed_off is True

    # correct credentials on locked session still rejected
    res4 = verify_customer(session, "user1@paylane.com", "4321")
    assert res4["ok"] is False
    assert res4["reason_code"] == "LOCKED"


def test_bug3_refund_blocked_after_lockout():
    """Refund must be blocked when session is handed off."""
    session = make_session(verified=True, customer_id="cust_101", handed_off=True)
    res = issue_refund(session, "TXN_1001")
    assert res["ok"] is False
    assert res["reason_code"] == "HANDED_OFF"


# --- Bug 4: Partial refunds prohibited ---
def test_bug4_partial_refund_rejected():
    """Partial amount (not full original) must be rejected."""
    session = make_session(verified=True, customer_id="cust_101")
    res = issue_refund(session, "TXN_1001", amount=20.00)  # full is 40.00
    assert res["ok"] is False
    assert res["reason_code"] == "PARTIAL_NOT_ALLOWED"


def test_bug4_zero_amount_rejected():
    """Zero amount must be rejected."""
    session = make_session(verified=True, customer_id="cust_101")
    res = issue_refund(session, "TXN_1001", amount=0.0)
    assert res["ok"] is False
    assert res["reason_code"] == "PARTIAL_NOT_ALLOWED"


def test_bug4_mismatched_amount_rejected_and_includes_transaction_amount():
    """Amount 14000 on TXN_1001 ($40.00) must be rejected with PARTIAL_NOT_ALLOWED
    and the response must include transaction_amount so the model can tell the customer."""
    session = make_session(verified=True, customer_id="cust_101")
    res = issue_refund(session, "TXN_1001", amount=14000.0)
    assert res["ok"] is False
    assert res["reason_code"] == "PARTIAL_NOT_ALLOWED"
    # transaction_amount must be present so the prompt can surface the correct figure
    assert "transaction_amount" in res
    assert abs(res["transaction_amount"] - 40.00) < 1e-4, (
        f"Expected transaction_amount ~40.00, got {res['transaction_amount']}"
    )


# --- Bug 5: Verification must not mutate shared mock DB ---
def test_bug5_verification_does_not_mutate_db():
    """Successful verify_customer must NOT set verified=True in the DB."""
    session = make_session()
    verify_customer(session, "user1@paylane.com", "4321")
    # The DB customer record must remain verified:False
    assert session.db["customers"]["user1@paylane.com"].get("verified") is False


def test_bug5_verification_state_not_leaked_between_sessions():
    """Verification state in one session must not affect another session."""
    s1 = SessionState(session_id="s1", db=get_fresh_db())
    s2 = SessionState(session_id="s2", db=get_fresh_db())
    verify_customer(s1, "user1@paylane.com", "4321")
    assert s1.verified is True
    assert s2.verified is False


# --- Bug 8: Fraud below $500 must escalate ---
def test_bug8_fraud_below_500_escalates():
    """Fraud report with $50 loss must escalate to Security & Fraud with normal priority."""
    from agent.triggers import check_deterministic_triggers
    session = make_session()
    result = check_deterministic_triggers("There is an unauthorized charge of $50 on my card.", session)
    assert result is not None
    assert result["trigger"] == "fraud"
    assert result["ticket"]["department"] == "Security & Fraud"
    assert result["ticket"]["priority"] == "normal"


def test_bug8_fraud_no_amount_escalates():
    """Fraud report with unknown amount must escalate at normal priority."""
    from agent.triggers import check_deterministic_triggers
    session = make_session()
    result = check_deterministic_triggers("Someone used my card without my permission!", session)
    assert result is not None
    assert result["trigger"] == "fraud"
    assert result["ticket"]["priority"] == "normal"


def test_bug8_how_do_you_prevent_fraud_does_not_trigger():
    """Generic fraud question must NOT trigger fraud escalation."""
    from agent.triggers import check_deterministic_triggers
    session = make_session()
    result = check_deterministic_triggers("How do you prevent fraud?", session)
    assert result is None


# --- Bug 9: escalate_to_human validates department ---
def test_bug9_invalid_department_raises_error():
    """Invalid department must raise ValueError."""
    session = make_session()
    with pytest.raises(ValueError, match="Invalid department"):
        escalate_to_human(session, summary="test", department="FakeDepartment")


def test_bug9_priority_computed_not_model_chosen():
    """Priority must be computed in code based on reported_loss_usd."""
    s1 = make_session()
    t1 = escalate_to_human(s1, "Fraud report", "Security & Fraud", reported_loss_usd=650.0)
    assert t1["priority"] == "high"

    s2 = make_session()
    t2 = escalate_to_human(s2, "Small charge", "Security & Fraud", reported_loss_usd=499.0)
    assert t2["priority"] == "normal"


# --- Bug 10: Future-dated transactions must be rejected ---
def test_bug10_future_dated_transaction_rejected():
    """Refund of a future-dated transaction must be rejected."""
    session = make_session(verified=True, customer_id="cust_101")
    res = issue_refund(session, "TXN_FUTURE")
    assert res["ok"] is False
    assert res["reason_code"] == "FUTURE_DATED"


def test_bug10_datetime_timezone_safety():
    """Elapsed time calculation must not raise TypeError from tz-naive/aware mix."""
    session = make_session(verified=True, customer_id="cust_101")
    # TXN_1004 is exactly 30 days old. Should return TOO_OLD, not raise TypeError.
    res = issue_refund(session, "TXN_1004")
    assert res["ok"] is False
    assert res["reason_code"] == "TOO_OLD"


# --- Bug 12: Account closure is an immediate trigger ---
def test_bug12_account_closure_triggers_without_verification():
    """Account closure must trigger immediate handoff to Account Operations."""
    from agent.triggers import check_deterministic_triggers
    session = make_session()
    result = check_deterministic_triggers("I want to close my account.", session)
    assert result is not None
    assert result["trigger"] == "account_closure"
    assert result["ticket"]["department"] == "Account Operations"
    assert session.handed_off is True


def test_bug12_legal_trigger_without_verification():
    """Legal action must trigger immediate handoff to Legal & Compliance."""
    from agent.triggers import check_deterministic_triggers
    session = make_session()
    result = check_deterministic_triggers("My lawyer is filing a lawsuit.", session)
    assert result is not None
    assert result["trigger"] == "legal"
    assert result["ticket"]["department"] == "Legal & Compliance"


def test_bug12_explicit_human_trigger_without_verification():
    """Explicit human request must trigger immediate handoff."""
    from agent.triggers import check_deterministic_triggers
    session = make_session()
    result = check_deterministic_triggers("I want to speak to a real person.", session)
    assert result is not None
    assert result["trigger"] == "explicit_human"
    assert result["ticket"]["department"] == "General Support"


# --- Bug 13: Deterministic IDs ---
def test_bug13_deterministic_ticket_ids():
    """Ticket IDs must be deterministic, not random."""
    s1 = make_session()
    s2 = make_session()
    reset_counters()
    t1 = escalate_to_human(s1, "test", "General Support")
    t2 = escalate_to_human(s2, "test", "Billing & Refunds")
    assert t1["ticket_id"] == "TCK-0001"
    assert t2["ticket_id"] == "TCK-0002"


# --- Bug 15: Output sanitization masks email and card numbers ---
def test_bug15_email_masked_in_output():
    """Email addresses must be masked in output."""
    text = "The user email is user1@paylane.com and card is 1234567812345678."
    sanitized = sanitize_output_pii(text)
    assert "user1@paylane.com" not in sanitized
    assert "[redacted]" in sanitized


def test_bug15_card_number_masked_in_output():
    """16-digit card numbers must be masked."""
    text = "Card number 4111111111111111 is on file."
    sanitized = sanitize_output_pii(text)
    assert "4111111111111111" not in sanitized
    assert "[redacted]" in sanitized


def test_bug15_four_digit_mention_preserved():
    """Bare 4-digit last4 references must NOT be masked."""
    text = "Your card ending in 4321 has been verified."
    sanitized = sanitize_output_pii(text)
    assert "4321" in sanitized


# --- Bug 16: Unknown email and wrong last-4 produce identical messages ---
def test_bug16_unknown_email_and_wrong_last4_identical_response():
    """Unknown email and wrong last-4 must produce identical response messages."""
    s1 = SessionState(session_id="s1", db=get_fresh_db())
    s2 = SessionState(session_id="s2", db=get_fresh_db())

    res_unknown_email = verify_customer(s1, "notexist@paylane.com", "4321")
    res_wrong_last4 = verify_customer(s2, "user1@paylane.com", "9999")

    assert res_unknown_email["ok"] is False
    assert res_wrong_last4["ok"] is False
    assert res_unknown_email["reason"] == res_wrong_last4["reason"]
    assert res_unknown_email["reason_code"] == res_wrong_last4["reason_code"]
