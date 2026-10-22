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


def make_session(verified=False, customer_id=None, handed_off=False, failed_attempts=0, locked=False):
    s = SessionState(session_id="test-reg", db=get_fresh_db())
    s.verified = verified
    s.customer_id = customer_id
    s.handed_off = handed_off
    s.failed_attempts = failed_attempts
    s.locked = locked
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
    assert session.locked is True
    assert session.ticket is None
    assert session.handed_off is False

    # correct credentials on locked session still rejected
    res4 = verify_customer(session, "user1@paylane.com", "4321")
    assert res4["ok"] is False
    assert res4["reason_code"] == "LOCKED"


def test_bug3_refund_blocked_after_lockout():
    """Refund must be blocked when session is locked."""
    session = make_session(verified=True, customer_id="cust_101", locked=True)
    res = issue_refund(session, "TXN_1001")
    assert res["ok"] is False
    assert res["reason_code"] == "LOCKED"


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


# --- Bug 8: Fraud below $500 must escalate (after verification) ---
def test_bug8_fraud_below_500_escalates():
    """Fraud report with $50 loss escalates to Security & Fraud (normal priority) once verified."""
    from agent.triggers import check_deterministic_triggers
    session = make_session()
    verify_customer(session, "user1@paylane.com", "4321")
    result = check_deterministic_triggers("There is an unauthorized charge of $50 on my card.", session)
    assert result is not None
    assert result["trigger"] == "fraud"
    assert result["ticket"]["department"] == "Security & Fraud"
    assert result["ticket"]["priority"] == "normal"


def test_bug8_fraud_no_amount_escalates():
    """Fraud report with unknown amount escalates at normal priority once verified."""
    from agent.triggers import check_deterministic_triggers
    session = make_session()
    verify_customer(session, "user1@paylane.com", "4321")
    result = check_deterministic_triggers("My card was stolen!", session)
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


def test_ec10_lockout_blocks_chat_without_human_escalation():
    """EC_10 verification: 3 wrong attempts lock the chat; does NOT escalate to human."""
    from paylane_api.sessions import session_store
    from agent.graph import agent_node
    from langchain_core.messages import HumanMessage

    sid = "test-ec10-lockout-sim"
    session = session_store.get_or_create(sid)

    # Turns 1 & 2: wrong attempts
    r1 = verify_customer(session, "user1@paylane.com", "9999")
    assert r1["failed_attempts"] == 1 and not session.locked and not session.handed_off
    r2 = verify_customer(session, "user1@paylane.com", "0000")
    assert r2["failed_attempts"] == 2 and not session.locked and not session.handed_off

    # Turn 3: 3rd wrong attempt triggers lockout
    r3 = verify_customer(session, "user1@paylane.com", "5555")
    assert r3["reason_code"] == "LOCKED"
    assert session.locked is True
    assert session.handed_off is False
    assert session.ticket is None

    # Turn 4: user continues to chat: "Now use card 4321."
    state = {
        "messages": [
            HumanMessage(content="My email is user1@paylane.com and card is 9999."),
            HumanMessage(content="Still me, card is 0000."),
            HumanMessage(content="Try card 5555."),
            HumanMessage(content="Now use card 4321."),
        ]
    }
    config = {"configurable": {"thread_id": sid}}
    result = agent_node(state, config=config)

    # Chat must be blocked without human escalation
    assert result["locked"] is True
    assert result["escalated"] is False
    assert result["ticket_details"] is None
    assert result["is_verified"] is False
    assert "locked" in result["messages"][0].content.lower()

    # All operations remain blocked
    assert issue_refund(session, "TXN_1001")["reason_code"] == "LOCKED"
    assert get_transaction(session, "TXN_1001")["reason_code"] == "LOCKED"
    assert verify_customer(session, "user1@paylane.com", "4321")["reason_code"] == "LOCKED"


def test_fraud_amount_mismatch_retry_and_lockout_end_to_end():
    """Screenshot scenario: user claims $650, verifies with user1, but TXN_1001 is $40.

    Must pause without escalating to human, prompt retry (Attempt 1 of 3),
    allow 2 more attempts, and lock session on 3rd failure.
    """
    from paylane_api.sessions import session_store
    from agent.graph import agent_node
    from langchain_core.messages import HumanMessage, ToolMessage
    import json

    sid = "test-fraud-mismatch-e2e"
    session = session_store.get_or_create(sid)

    # Turn 1: claim $650
    state1 = {"messages": [HumanMessage(content="I have unauthorized charges totalling $650 on my account!")]}
    res1 = agent_node(state1, config={"configurable": {"thread_id": sid}})
    assert session.pending_fraud == {"loss_usd": 650.0, "txn_id": None}
    assert session.handed_off is False
    assert session.ticket is None

    # Verify customer user1 with credentials
    verify_customer(session, "user1@paylane.com", "4321")
    assert session.verified is True

    # Turn 2: user provides TXN_1001 ($40.00 != $650)
    # Simulate get_customer tool response in LangGraph flow
    tool_msg = ToolMessage(
        content=json.dumps({"ok": True, "name": "Alex Morgan", "card_status": "active"}),
        name="get_customer",
        tool_call_id="call_test1",
    )
    state2 = {
        "messages": [
            HumanMessage(content="I have unauthorized charges totalling $650 on my account!"),
            HumanMessage(content="My email is user1@paylane.com and card ends in 4321.TXN_1001."),
            tool_msg,
        ]
    }
    res2 = agent_node(state2, config={"configurable": {"thread_id": sid}})

    # Must NOT escalate to human!
    assert res2["escalated"] is False
    assert res2["ticket_details"] is None
    assert session.handed_off is False
    assert session.ticket is None
    assert session.failed_attempts == 1
    reply2 = res2["messages"][0].content
    assert "$40.00" in reply2
    assert "650" in reply2
    assert "Attempt 1 of 3" in reply2

    # Turn 3: 2nd attempt with mismatched transaction TXN_1002 ($75.00 != $650)
    state3 = {
        "messages": state2["messages"] + [
            res2["messages"][0],
            HumanMessage(content="Check TXN_1002."),
        ]
    }
    res3 = agent_node(state3, config={"configurable": {"thread_id": sid}})
    assert res3["escalated"] is False
    assert res3["ticket_details"] is None
    assert session.handed_off is False
    assert session.ticket is None
    assert session.failed_attempts == 2
    assert "Attempt 2 of 3" in res3["messages"][0].content

    # Turn 4: 3rd attempt with mismatched transaction TXN_1003 ($75.01 != $650)
    state4 = {
        "messages": state3["messages"] + [
            res3["messages"][0],
            HumanMessage(content="Check TXN_1003."),
        ]
    }
    res4 = agent_node(state4, config={"configurable": {"thread_id": sid}})
    assert res4["escalated"] is False
    assert res4["ticket_details"] is None
    assert session.handed_off is False
    assert session.ticket is None
    assert session.failed_attempts == 3
    assert session.locked is True
    assert "locked" in res4["messages"][0].content.lower()

    # Turn 5: further messages are completely blocked by the locked guard
    state5 = {"messages": [HumanMessage(content="Can you help me now?")]}
    res5 = agent_node(state5, config={"configurable": {"thread_id": sid}})
    assert res5["locked"] is True
    assert "locked" in res5["messages"][0].content.lower()


def test_fraud_no_matching_transaction_on_account_pauses_without_escalation():
    """User reports $650, authenticates with user1/4321, but user1 owns NO $650 transaction.

    Must NOT escalate to human just based on authentication!
    Must pause and prompt retry (Attempt 1 of 3).
    """
    from paylane_api.sessions import session_store
    from agent.graph import agent_node
    from langchain_core.messages import HumanMessage, ToolMessage
    import json

    sid = "test-fraud-no-txn-match"
    session = session_store.get_or_create(sid)

    # Turn 1: user reports $7500 (an amount not owned by user1)
    state1 = {"messages": [HumanMessage(content="I have unauthorized charges totalling $7500 on my account!")]}
    agent_node(state1, config={"configurable": {"thread_id": sid}})
    assert session.pending_fraud == {"loss_usd": 7500.0, "txn_id": None}

    # Verify user1
    verify_customer(session, "user1@paylane.com", "4321")
    assert session.verified is True

    # Turn 2: user only provides credentials: "My email is user1@paylane.com and card ends in 4321."
    tool_msg = ToolMessage(
        content=json.dumps({"ok": True, "name": "Alex Morgan", "card_status": "active"}),
        name="get_customer",
        tool_call_id="call_test2",
    )
    state2 = {
        "messages": [
            HumanMessage(content="I have unauthorized charges totalling $7500 on my account!"),
            HumanMessage(content="My email is user1@paylane.com and card ends in 4321."),
            tool_msg,
        ]
    }
    res2 = agent_node(state2, config={"configurable": {"thread_id": sid}})

    # MUST NOT escalate to human just because of authentication!
    assert res2["escalated"] is False
    assert res2["ticket_details"] is None
    assert session.handed_off is False
    assert session.ticket is None
    assert session.failed_attempts == 1
    reply2 = res2["messages"][0].content
    assert "could not find any transaction" in reply2.lower()
    assert "Attempt 1 of 3" in reply2


def test_fraud_real_payment_650_on_verification_escalates_to_human():
    """Real 650 case: cust_101 has TXN_1012 ($650.00).

    On verification, the 650 payment is found in DB, so it escalates to human (Security & Fraud, high priority).
    """
    from paylane_api.sessions import session_store
    from agent.graph import agent_node
    from langchain_core.messages import HumanMessage, ToolMessage
    import json

    sid = "test-fraud-real-650-match"
    session = session_store.get_or_create(sid)

    # Turn 1: user reports $650
    state1 = {"messages": [HumanMessage(content="I have unauthorized charges totalling $650 on my account!")]}
    agent_node(state1, config={"configurable": {"thread_id": sid}})
    assert session.pending_fraud == {"loss_usd": 650.0, "txn_id": None}

    # Verify user1
    verify_customer(session, "user1@paylane.com", "4321")
    assert session.verified is True

    # Turn 2: user provides credentials: "My email is user1@paylane.com and card ends in 4321."
    tool_msg = ToolMessage(
        content=json.dumps({"ok": True, "name": "Alex Morgan", "card_status": "active"}),
        name="get_customer",
        tool_call_id="call_test3",
    )
    state2 = {
        "messages": [
            HumanMessage(content="I have unauthorized charges totalling $650 on my account!"),
            HumanMessage(content="My email is user1@paylane.com and card ends in 4321."),
            tool_msg,
        ]
    }
    res2 = agent_node(state2, config={"configurable": {"thread_id": sid}})

    # Real payment found in DB -> escalates to Security & Fraud with high priority!
    assert res2["escalated"] is True
    assert res2["ticket_details"] is not None
    assert res2["ticket_details"]["department"] == "Security & Fraud"
    assert res2["ticket_details"]["priority"] == "high"
    assert session.handed_off is True
    assert session.ticket is not None
    reply2 = res2["messages"][0].content
    assert "escalated to our security & fraud team" in reply2.lower()