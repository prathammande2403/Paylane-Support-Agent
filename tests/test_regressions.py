"""
tests/test_regressions.py
Regression tests reproducing the 16 known bugs listed in Section 1.
These tests assert the correct policy and architecture behavior.
When run against the original baseline code, these tests MUST FAIL.
Once the core and service layer are refactored, these tests MUST PASS.
"""

from datetime import timedelta
import pytest
from langchain_core.messages import HumanMessage

from data.mock_db import (
    FIXED_TEST_CLOCK,
    MOCK_CUSTOMERS,
    MOCK_TRANSACTIONS,
    reset_mock_db,
)
from agent.tools import (
    get_customer,
    get_transaction,
    issue_refund,
    escalate_to_human,
)
from agent.guardrails import sanitize_output_pii


@pytest.fixture(autouse=True)
def clean_db():
    reset_mock_db()


def test_bug1_unverified_user_cannot_read_or_refund_transaction():
    """
    Bug 1: get_transaction and issue_refund skip ownership check when cust_id is None,
    and never check is_verified. Unverified users can read or refund any transaction.
    Expected: Unverified calls must fail with ok=False.
    """
    # Unverified read attempt
    res_read = get_transaction.invoke({"txn_id": "TXN_1001"})
    assert res_read.get("ok") is False, f"Expected unverified read to fail, got {res_read}"

    # Unverified refund attempt
    res_refund = issue_refund.invoke({"txn_id": "TXN_1001"})
    assert res_refund.get("ok") is False, f"Expected unverified refund to fail, got {res_refund}"


def test_bug2_customer_id_not_in_tool_signatures():
    """
    Bug 2: The LLM can pass customer_id to tools and override session value.
    Expected: customer_id must be removed from model-visible tool signatures.
    """
    assert "customer_id" not in get_transaction.args, "customer_id should not be in get_transaction model args"
    assert "customer_id" not in issue_refund.args, "customer_id should not be in issue_refund model args"
    assert "customer_id" not in escalate_to_human.args, "customer_id should not be in escalate_to_human model args"


def test_bug3_three_strike_lockout_and_escalated_state():
    """
    Bug 3: 3-strike lock does not lock: get_customer still works after 3 failures,
    and refund tools do not check escalated.
    Expected: Once locked/escalated, further get_customer or refund attempts must be blocked.
    """
    # Try with state already having escalated/3 failures, attempting to verify with correct credentials
    call_payload = {
        "name": "get_customer",
        "args": {"email": "user1@paylane.com", "card_last4": "4321"},
        "id": "call_locked",
        "type": "tool_call",
    }
    cmd = get_customer.invoke(
        call_payload,
        state={"failed_attempts": 3, "escalated": True, "customer_id": "cust_101"}
    )
    # On original code, cmd verifies user1@paylane.com and returns is_verified: True!
    verified = False
    if hasattr(cmd, "update"):
        verified = cmd.update.get("is_verified", False)
    elif isinstance(cmd, dict):
        verified = cmd.get("ok", False)
    assert not verified, "Locked session must NOT allow verification"

    # Also test that refund tool checks escalated
    res = issue_refund.invoke(
        {"txn_id": "TXN_1001"},
        state={"escalated": True, "customer_id": "cust_101", "is_verified": True}
    )
    assert res.get("ok") is False, "Refund must not proceed when session is escalated/locked"


def test_bug4_partial_refunds_prohibited():
    """
    Bug 4: Partial refunds are possible through the amount parameter.
    Expected: Only full transaction amount may be refunded.
    """
    res = issue_refund.invoke(
        {"txn_id": "TXN_1001", "amount": 10.00},
        state={"is_verified": True, "customer_id": "cust_101"}
    )
    assert res.get("ok") is False, f"Expected partial refund to be rejected, got: {res}"


def test_bug5_verification_does_not_mutate_shared_mock_db():
    """
    Bug 5: customer['verified'] = True mutates the shared mock DB, leaking across sessions.
    Expected: Verification state must not mutate the shared mock DB.
    """
    call_payload = {
        "name": "get_customer",
        "args": {"email": "user1@paylane.com", "card_last4": "4321"},
        "id": "call_verify",
        "type": "tool_call",
    }
    get_customer.invoke(call_payload)
    # Check shared mock dict
    assert MOCK_CUSTOMERS["user1@paylane.com"].get("verified") is False, (
        "MOCK_CUSTOMERS should not have verified=True mutated in place"
    )


def test_bug8_fraud_unauthorized_below_500_must_escalate():
    """
    Bug 8: Fraud amount regex treats any number as dollar amount,
    and suspected unauthorized charges below $500 are not forced to human.
    Expected: Suspected unauthorized charge below $500 must escalate to Security & Fraud (normal priority).
    """
    from agent.graph import paylane_graph
    
    state = {
        "messages": [HumanMessage(content="There is an unauthorized charge of $50 on my card.")],
        "customer_id": None,
        "email_provided": None,
        "card_last4_provided": None,
        "is_verified": False,
        "failed_attempts": 0,
        "unresolved_turns": 0,
        "escalated": False,
        "ticket_details": None,
    }
    result = paylane_graph.invoke(state)
    assert result.get("escalated") is True, "Suspected unauthorized charge below $500 must escalate"
    ticket = result.get("ticket_details") or {}
    assert ticket.get("department") == "Security & Fraud"


def test_bug9_escalate_to_human_validates_department():
    """
    Bug 9: escalate_to_human lets model choose priority and department freely.
    Expected: Department must be validated against allowed set and priority computed in code.
    """
    ALLOWED_DEPTS = {
        "Billing & Refunds",
        "Security & Fraud",
        "Legal & Compliance",
        "Account Operations",
        "General Support",
    }
    call_payload = {
        "name": "escalate_to_human",
        "args": {"department": "FakeDepartment", "summary": "test", "priority": "urgent"},
        "id": "call_esc",
        "type": "tool_call",
    }
    res = escalate_to_human.invoke(call_payload)
    dept = ""
    if hasattr(res, "update"):
        dept = res.update.get("ticket_details", {}).get("department", "")
    elif isinstance(res, dict):
        dept = res.get("department", "")
    assert dept in ALLOWED_DEPTS, f"Department '{dept}' must be one of allowed set"


def test_bug10_future_dated_transaction_rejected():
    """
    Bug 10: Future-dated transactions pass with negative age.
    Expected: Future-dated transactions must be rejected with ok=False.
    """
    future_date = (FIXED_TEST_CLOCK + timedelta(days=5)).isoformat()
    MOCK_TRANSACTIONS["TXN_FUTURE"] = {
        "txn_id": "TXN_FUTURE",
        "customer_id": "cust_101",
        "amount": 40.00,
        "date": future_date,
        "merchant": "Future Store",
        "status": "settled",
    }
    res = issue_refund.invoke(
        {"txn_id": "TXN_FUTURE"},
        state={"is_verified": True, "customer_id": "cust_101"}
    )
    assert res.get("ok") is False, f"Expected future-dated refund to be rejected, got: {res}"


def test_bug12_account_closure_immediate_handoff():
    """
    Bug 12: Account closure is not an emergency trigger.
    Expected: Account closure must hand off immediately without verification to Account Operations.
    """
    from agent.graph import paylane_graph
    
    state = {
        "messages": [HumanMessage(content="I want to close my account immediately.")],
        "customer_id": None,
        "email_provided": None,
        "card_last4_provided": None,
        "is_verified": False,
        "failed_attempts": 0,
        "unresolved_turns": 0,
        "escalated": False,
        "ticket_details": None,
    }
    result = paylane_graph.invoke(state)
    assert result.get("escalated") is True, "Account closure must trigger immediate handoff"
    ticket = result.get("ticket_details") or {}
    assert ticket.get("department") == "Account Operations"


def test_bug15_output_sanitization_masks_email():
    """
    Bug 15: Output filter does not mask email addresses.
    Expected: Emails in agent output must be masked.
    """
    text = "The user email is user1@paylane.com and card is 1234567812345678."
    sanitized = sanitize_output_pii(text)
    assert "user1@paylane.com" not in sanitized, "Email should be masked in output"
    assert "[redacted]" in sanitized or "****" in sanitized
