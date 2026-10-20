"""
tests/test_service.py
Deterministic unit tests for the pure-Python Paylane service layer (paylane_api/service.py).
Tests every guard, boundary, security invariant, and error condition without calling any LLM.
"""

from datetime import timedelta
import pytest
from data.mock_db import FIXED_TEST_CLOCK, get_fresh_db
from paylane_api.service import (
    SessionState,
    verify_customer,
    get_transaction,
    issue_refund,
    escalate_to_human,
    reset_counters,
    strip_pii_from_text,
)


@pytest.fixture(autouse=True)
def setup_teardown():
    reset_counters()


def create_test_session(session_id="test-session"):
    return SessionState(session_id=session_id, db=get_fresh_db())


def test_unverified_read_and_refund_blocked():
    session = create_test_session()
    read_res = get_transaction(session, "TXN_1001")
    assert read_res["ok"] is False
    assert read_res["reason_code"] == "UNVERIFIED"

    refund_res = issue_refund(session, "TXN_1001")
    assert refund_res["ok"] is False
    assert refund_res["reason_code"] == "UNVERIFIED"


def test_identity_cannot_be_overridden():
    session = create_test_session()
    # Verify as user1 (cust_101)
    v_res = verify_customer(session, "user1@paylane.com", "4321")
    assert v_res["ok"] is True
    assert session.customer_id == "cust_101"

    # TXN_2001 belongs to cust_104
    read_res = get_transaction(session, "TXN_2001")
    assert read_res["ok"] is False
    assert read_res["reason_code"] == "NOT_FOUND"

    refund_res = issue_refund(session, "TXN_2001")
    assert refund_res["ok"] is False
    assert refund_res["reason_code"] == "NOT_FOUND"


def test_wrong_email_and_wrong_last4_give_identical_messages():
    session1 = create_test_session("s1")
    session2 = create_test_session("s2")

    # Unknown email
    res1 = verify_customer(session1, "unknown@paylane.com", "4321")
    # Wrong last4
    res2 = verify_customer(session2, "user1@paylane.com", "9999")

    assert res1["ok"] is False
    assert res2["ok"] is False
    assert res1["reason"] == res2["reason"]
    assert res1["reason_code"] == res2["reason_code"] == "MISMATCH"


def test_three_strike_lockout_then_correct_credentials_rejected():
    session = create_test_session()

    # Attempt 1
    r1 = verify_customer(session, "user1@paylane.com", "0000")
    assert r1["ok"] is False
    assert session.failed_attempts == 1

    # Attempt 2
    r2 = verify_customer(session, "user1@paylane.com", "0000")
    assert r2["ok"] is False
    assert session.failed_attempts == 2

    # Attempt 3 - locks session
    r3 = verify_customer(session, "user1@paylane.com", "0000")
    assert r3["ok"] is False
    assert r3["reason_code"] == "LOCKED"
    assert session.handed_off is True
    assert session.ticket is not None
    assert session.ticket["department"] == "Account Operations"
    assert session.ticket["priority"] == "high"

    # Attempt 4 - correct credentials
    r4 = verify_customer(session, "user1@paylane.com", "4321")
    assert r4["ok"] is False
    assert r4["reason_code"] == "LOCKED"
    assert session.verified is False


def test_refund_amount_boundaries():
    session = create_test_session()
    verify_customer(session, "user1@paylane.com", "4321")

    # $75.00 auto-approved (TXN_1002, 29 days old)
    r75 = issue_refund(session, "TXN_1002")
    assert r75["ok"] is True
    assert r75["refund_id"] == "REF-0001"

    # $75.01 escalates (TXN_1003, 10 days old)
    r7501 = issue_refund(session, "TXN_1003")
    assert r7501["ok"] is False
    assert r7501["reason_code"] == "ABOVE_LIMIT"


def test_refund_time_boundaries():
    session = create_test_session()
    verify_customer(session, "user1@paylane.com", "4321")

    # 29 days old (TXN_1002) is approved
    r29 = issue_refund(session, "TXN_1002")
    assert r29["ok"] is True

    # Exactly 30 days old (TXN_1004) escalates
    r30 = issue_refund(session, "TXN_1004")
    assert r30["ok"] is False
    assert r30["reason_code"] == "TOO_OLD"


def test_future_dated_transaction_rejected():
    session = create_test_session()
    verify_customer(session, "user1@paylane.com", "4321")

    # TXN_FUTURE is dated in future
    res = issue_refund(session, "TXN_FUTURE")
    assert res["ok"] is False
    assert res["reason_code"] == "FUTURE_DATED"


def test_partial_and_zero_negative_amounts_rejected():
    session = create_test_session()
    verify_customer(session, "user1@paylane.com", "4321")

    # Full amount is 40.00
    r_part = issue_refund(session, "TXN_1001", amount=20.00)
    assert r_part["ok"] is False
    assert r_part["reason_code"] == "PARTIAL_NOT_ALLOWED"

    r_zero = issue_refund(session, "TXN_1001", amount=0.0)
    assert r_zero["ok"] is False
    assert r_zero["reason_code"] == "PARTIAL_NOT_ALLOWED"

    r_neg = issue_refund(session, "TXN_1001", amount=-10.0)
    assert r_neg["ok"] is False
    assert r_neg["reason_code"] == "PARTIAL_NOT_ALLOWED"


def test_pending_rejected():
    session = create_test_session()
    verify_customer(session, "user1@paylane.com", "4321")

    res = issue_refund(session, "TXN_1005")
    assert res["ok"] is False
    assert res["reason_code"] == "PENDING"


def test_double_refund_rejected_and_idempotent():
    session = create_test_session()
    verify_customer(session, "user1@paylane.com", "4321")

    # First refund succeeds
    r1 = issue_refund(session, "TXN_1001")
    assert r1["ok"] is True

    # Second refund for same txn is rejected
    r2 = issue_refund(session, "TXN_1001")
    assert r2["ok"] is False
    assert r2["reason_code"] == "ALREADY_REFUNDED"


def test_no_refund_after_handoff():
    session = create_test_session()
    verify_customer(session, "user1@paylane.com", "4321")

    # Handoff occurs
    escalate_to_human(session, summary="Customer requested agent", department="General Support")
    assert session.handed_off is True

    # Subsequent refund attempt blocked
    res = issue_refund(session, "TXN_1001")
    assert res["ok"] is False
    assert res["reason_code"] == "HANDED_OFF"


def test_second_escalation_returns_same_ticket():
    session = create_test_session()
    t1 = escalate_to_human(session, summary="First issue", department="General Support")
    assert t1["ticket_id"] == "TCK-0001"

    t2 = escalate_to_human(session, summary="Second issue", department="Billing & Refunds")
    assert t2["ticket_id"] == "TCK-0001"
    assert t1 == t2


def test_priority_computation():
    session1 = create_test_session("s1")
    t1 = escalate_to_human(session1, summary="Fraud report", department="Security & Fraud", reported_loss_usd=650.0)
    assert t1["priority"] == "high"

    session2 = create_test_session("s2")
    t2 = escalate_to_human(session2, summary="Small charge", department="Security & Fraud", reported_loss_usd=499.0)
    assert t2["priority"] == "normal"

    session3 = create_test_session("s3")
    t3 = escalate_to_human(session3, summary="Unknown amount", department="Security & Fraud", reported_loss_usd=None)
    assert t3["priority"] == "normal"


def test_summaries_stripped_of_pii():
    session = create_test_session()
    raw_summary = "Fraud on user1@paylane.com with card 4111222233334444 ending in 4444"
    ticket = escalate_to_human(session, summary=raw_summary, department="Security & Fraud")
    assert "user1@paylane.com" not in ticket["summary"]
    assert "4111222233334444" not in ticket["summary"]
    assert "[redacted]" in ticket["summary"]
    assert "4444" in ticket["summary"]  # Bare 4 digits preserved


def test_verification_state_does_not_leak_between_sessions():
    session1 = create_test_session("s1")
    session2 = create_test_session("s2")

    verify_customer(session1, "user1@paylane.com", "4321")
    assert session1.verified is True

    # session2 should be completely clean
    assert session2.verified is False
    assert session2.customer_id is None
    # DB record verified is still False
    assert session1.db["customers"]["user1@paylane.com"]["verified"] is False
    assert session2.db["customers"]["user1@paylane.com"]["verified"] is False
