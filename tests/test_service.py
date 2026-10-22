"""
tests/test_service.py
Deterministic unit tests for the pure-Python Paylane service layer (paylane_api/service.py).
Tests every guard, boundary, security invariant, and error condition without calling any LLM.
"""

import pytest
from data.mock_db import get_fresh_db
from paylane_api.service import (
    SessionState,
    verify_customer,
    get_transaction,
    issue_refund,
    escalate_to_human,
    reset_counters,
)

# ---------------------------------------------------------------------------
# Seed data used by these tests. Defined ONCE here; must match data/mock_db.py.
# If the seed data changes, this is the only block you need to edit.
# ---------------------------------------------------------------------------
EMAIL = "user1@paylane.com"
LAST4 = "4321"
CUSTOMER_ID = "cust_101"

TXN_BASIC = "TXN_1001"            # $40.00, refundable
TXN_AT_LIMIT = "TXN_1002"         # $75.00, 29 days old -> auto-approved
TXN_OVER_LIMIT = "TXN_1003"       # $75.01, 10 days old -> above limit
TXN_30_DAYS_OLD = "TXN_1004"      # exactly 30 days old -> too old
TXN_PENDING = "TXN_1005"          # still pending
TXN_FUTURE = "TXN_FUTURE"         # dated in the future
TXN_OTHER_CUSTOMER = "TXN_2001"   # belongs to cust_104, not cust_101


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def reset_state():
    reset_counters()


@pytest.fixture
def make_session():
    """Factory for fresh sessions, for tests that need more than one."""
    def _make(session_id="test-session"):
        return SessionState(session_id=session_id, db=get_fresh_db())
    return _make


@pytest.fixture
def session(make_session):
    return make_session()


@pytest.fixture
def verified_session(session):
    result = verify_customer(session, EMAIL, LAST4)
    assert result["ok"] is True
    return session


# ---------------------------------------------------------------------------
# Verification and identity
# ---------------------------------------------------------------------------
def test_unverified_read_and_refund_blocked(session):
    read_res = get_transaction(session, TXN_BASIC)
    assert read_res["ok"] is False
    assert read_res["reason_code"] == "UNVERIFIED"

    refund_res = issue_refund(session, TXN_BASIC)
    assert refund_res["ok"] is False
    assert refund_res["reason_code"] == "UNVERIFIED"


def test_identity_cannot_be_overridden(verified_session):
    assert verified_session.customer_id == CUSTOMER_ID

    read_res = get_transaction(verified_session, TXN_OTHER_CUSTOMER)
    assert read_res["ok"] is False
    assert read_res["reason_code"] == "NOT_FOUND"

    refund_res = issue_refund(verified_session, TXN_OTHER_CUSTOMER)
    assert refund_res["ok"] is False
    assert refund_res["reason_code"] == "NOT_FOUND"


def test_wrong_email_and_wrong_last4_give_identical_messages(make_session):
    unknown_email = verify_customer(make_session("s1"), "unknown@paylane.com", LAST4)
    wrong_last4 = verify_customer(make_session("s2"), EMAIL, "9999")

    assert unknown_email["ok"] is False
    assert wrong_last4["ok"] is False
    assert unknown_email["reason"] == wrong_last4["reason"]
    assert unknown_email["reason_code"] == wrong_last4["reason_code"] == "MISMATCH"


def test_three_strike_lockout_then_correct_credentials_rejected(session):
    for expected_failures in (1, 2):
        res = verify_customer(session, EMAIL, "0000")
        assert res["ok"] is False
        assert session.failed_attempts == expected_failures

    # Third failure locks the session without escalating to human
    r3 = verify_customer(session, EMAIL, "0000")
    assert r3["ok"] is False
    assert r3["reason_code"] == "LOCKED"
    assert session.locked is True
    assert session.handed_off is False
    assert session.ticket is None

    # Correct credentials no longer work once locked
    r4 = verify_customer(session, EMAIL, LAST4)
    assert r4["ok"] is False
    assert r4["reason_code"] == "LOCKED"
    assert session.verified is False


def test_verification_state_does_not_leak_between_sessions(make_session):
    session1 = make_session("s1")
    session2 = make_session("s2")

    verify_customer(session1, EMAIL, LAST4)
    assert session1.verified is True

    assert session2.verified is False
    assert session2.customer_id is None
    # The DB record's verified flag is untouched in both sessions
    assert session1.db["customers"][EMAIL]["verified"] is False
    assert session2.db["customers"][EMAIL]["verified"] is False


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------
def test_refund_at_amount_limit_is_approved(verified_session):
    """$75.00 is auto-approved."""
    res = issue_refund(verified_session, TXN_AT_LIMIT)
    assert res["ok"] is True
    assert res["refund_id"] == "REF-0001"


@pytest.mark.parametrize("txn_id, reason_code", [
    (TXN_OVER_LIMIT, "ABOVE_LIMIT"),       # $75.01
    (TXN_30_DAYS_OLD, "TOO_OLD"),          # exactly 30 days
    (TXN_FUTURE, "FUTURE_DATED"),
    (TXN_PENDING, "PENDING"),
])
def test_refund_rejections(verified_session, txn_id, reason_code):
    res = issue_refund(verified_session, txn_id)
    assert res["ok"] is False
    assert res["reason_code"] == reason_code


@pytest.mark.parametrize("amount", [20.00, 0.0, -10.0])
def test_partial_zero_and_negative_amounts_rejected(verified_session, amount):
    """Full amount of TXN_BASIC is 40.00; anything else is not allowed."""
    res = issue_refund(verified_session, TXN_BASIC, amount=amount)
    assert res["ok"] is False
    assert res["reason_code"] == "PARTIAL_NOT_ALLOWED"


def test_double_refund_rejected_and_idempotent(verified_session):
    assert issue_refund(verified_session, TXN_BASIC)["ok"] is True

    r2 = issue_refund(verified_session, TXN_BASIC)
    assert r2["ok"] is False
    assert r2["reason_code"] == "ALREADY_REFUNDED"


def test_no_refund_after_handoff(verified_session):
    escalate_to_human(verified_session, summary="Customer requested agent", department="General Support")
    assert verified_session.handed_off is True

    res = issue_refund(verified_session, TXN_BASIC)
    assert res["ok"] is False
    assert res["reason_code"] == "HANDED_OFF"


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------
def test_second_escalation_returns_same_ticket(session):
    t1 = escalate_to_human(session, summary="First issue", department="General Support")
    assert t1["ticket_id"] == "TCK-0001"

    t2 = escalate_to_human(session, summary="Second issue", department="Billing & Refunds")
    assert t2["ticket_id"] == "TCK-0001"
    assert t1 == t2


@pytest.mark.parametrize("loss, priority", [
    (650.0, "high"),
    (499.0, "normal"),
    (None, "normal"),
])
def test_priority_computation(make_session, loss, priority):
    ticket = escalate_to_human(
        make_session(),
        summary="Fraud report",
        department="Security & Fraud",
        reported_loss_usd=loss,
    )
    assert ticket["priority"] == priority


def test_summaries_stripped_of_pii(session):
    raw_summary = "Fraud on user1@paylane.com with card 4111222233334444 ending in 4444"
    ticket = escalate_to_human(session, summary=raw_summary, department="Security & Fraud")
    assert "user1@paylane.com" not in ticket["summary"]
    assert "4111222233334444" not in ticket["summary"]
    assert "[redacted]" in ticket["summary"]
    assert "4444" in ticket["summary"]  # bare last 4 digits preserved