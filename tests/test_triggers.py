"""
tests/test_triggers.py
Tests for deterministic emergency triggers (legal, closure, human, fraud),
the frozen-card trigger, the refund escalation backstop, and the verification-retry backstop.
No LLM required.
"""

import pytest
from paylane_api.service import SessionState, reset_counters
from data.mock_db import get_fresh_db
from agent.triggers import (
    check_deterministic_triggers,
    parse_reported_dollar_amounts,
    check_frozen_card_trigger,
    check_verification_retry,
    check_pending_fraud_trigger,
    check_refund_escalation_trigger,
)


@pytest.fixture(autouse=True)
def setup():
    reset_counters()


def make_session(**kwargs):
    return SessionState(session_id="trigger-test", db=get_fresh_db(), **kwargs)


# --- Legal triggers ---
@pytest.mark.parametrize("message", [
    "I have a lawyer and will sue you.",
    "I'm filing a lawsuit against Paylane.",
    "My attorney will contact you shortly.",
    "I'll report this to the regulator.",
    "I'm filing a CFPB complaint.",
    "This matter will be decided in court.",
    "My solicitor will be in touch.",
])
def test_legal_triggers(message):
    s = make_session()
    assert s.verified is False  # legal handoff must not require verification
    res = check_deterministic_triggers(message, s)
    assert res is not None
    assert res["trigger"] == "legal"
    assert res["ticket"]["department"] == "Legal & Compliance"
    assert res["ticket"]["customer_id"] == "GUEST"
    assert s.handed_off is True


# --- Account closure triggers ---
@pytest.mark.parametrize("message", [
    "I want to close my account.",
    "Please delete my account immediately.",
    "I'd like to cancel my Paylane account.",
    "Close my account.",
])
def test_closure_triggers(message):
    s = make_session()
    res = check_deterministic_triggers(message, s)
    assert res is not None
    assert res["trigger"] == "account_closure"
    assert res["ticket"]["department"] == "Account Operations"
    assert s.handed_off is True


# --- Explicit human triggers ---
@pytest.mark.parametrize("message", [
    "I want to speak to someone.",
    "I need to talk to a real person.",
    "Connect me to a human representative.",
    "I don't want to talk to an AI.",
    "I want a human agent.",
])
def test_human_triggers(message):
    s = make_session()
    res = check_deterministic_triggers(message, s)
    assert res is not None
    assert res["trigger"] == "explicit_human"
    assert res["ticket"]["department"] == "General Support"
    assert s.handed_off is True


# --- Fraud triggers (verification required before escalation) ---
@pytest.mark.parametrize("message, priority, loss", [
    ("There were unauthorized charges totalling $650.", "high", 650.0),
    ("I see an unauthorized charge of $200.", "normal", 200.0),
    ("Someone used my card, I see unauthorized activity.", "normal", None),
])
def test_fraud_priority_and_loss_after_verification(message, priority, loss):
    """Unverified fraud report is held; after verification it escalates (loss > $500 -> high)."""
    from paylane_api.service import verify_customer
    s = make_session()
    if loss is not None:
        s.db["transactions"]["TXN_FRAUD"] = {
            "txn_id": "TXN_FRAUD",
            "customer_id": "cust_101",
            "amount": loss,
            "status": "settled",
        }
    res = check_deterministic_triggers(message, s)
    assert res is not None and res["trigger"] == "fraud_pending_verification"
    assert res["ticket"] is None
    assert s.handed_off is False
    assert s.pending_fraud == {"loss_usd": loss}
    assert verify_customer(s, "user1@paylane.com", "4321")["ok"]
    res = check_pending_fraud_trigger(s)
    assert res["trigger"] == "fraud"
    assert res["ticket"]["department"] == "Security & Fraud"
    assert res["ticket"]["priority"] == priority
    assert res["ticket"]["reported_loss_usd"] == loss


def test_fraud_650_not_escalated_before_verification():
    s = make_session()
    res = check_deterministic_triggers("I have unauthorized charges totalling $650 on my account!", s)
    assert res["trigger"] == "fraud_pending_verification"
    assert "email" in res["reply"].lower() and "card" in res["reply"].lower() and "transaction" in res["reply"].lower()
    assert s.handed_off is False and s.ticket is None


def test_fraud_failed_verification_does_not_escalate_fraud():
    from paylane_api.service import verify_customer
    s = make_session()
    check_deterministic_triggers("Unauthorized charges of $650.", s)
    assert not verify_customer(s, "user1@paylane.com", "0000")["ok"]
    assert check_pending_fraud_trigger(s) is None
    assert s.handed_off is False


def test_fraud_with_credentials_in_same_message_goes_to_verification():
    s = make_session()
    res = check_deterministic_triggers(
        "Unauthorized $650 charges. Email user1@paylane.com, card 4321.", s)
    assert res is None  # LLM/tool verification flow runs next
    assert s.pending_fraud == {"loss_usd": 650.0}
    assert s.handed_off is False


def test_fraud_already_verified_escalates_immediately():
    from paylane_api.service import verify_customer
    s = make_session()
    verify_customer(s, "user1@paylane.com", "4321")
    res = check_deterministic_triggers("There were unauthorized charges totalling $650.", s)
    assert res["trigger"] == "fraud" and res["ticket"]["priority"] == "high"


def test_fraud_claimed_amount_mismatch_pauses_and_locks_after_3_attempts():
    """Fraud scenario: user claims $650 loss, but referenced txns sum to $115.

    Must NOT escalate to human on mismatch. It must pause, allow retry up to 3 times,
    and lock the session on the 3rd failed attempt.
    """
    from paylane_api.service import verify_customer
    s = make_session()
    # Turn 1: claim $650
    check_deterministic_triggers("I have unauthorized charges totalling $650 on my account!", s)
    assert s.pending_fraud == {"loss_usd": 650.0}

    # Verify user1
    verify_customer(s, "user1@paylane.com", "4321")

    # Attempt 1: provides TXN_1001 ($40) + TXN_1002 ($75) = $115 != $650
    res1 = check_pending_fraud_trigger(
        s,
        user_text="My email is user1@paylane.com, card ending 4321. The charges are TXN_1001 and TXN_1002.",
    )
    assert res1 is not None
    assert res1["trigger"] == "fraud_mismatch_retry"
    assert res1["ticket"] is None
    assert s.handed_off is False
    assert s.failed_attempts == 1
    assert "Attempt 1 of 3" in res1["reply"]

    # Attempt 2: still mismatch
    res2 = check_pending_fraud_trigger(s, user_text="Check TXN_1001.")
    assert res2["trigger"] == "fraud_mismatch_retry"
    assert res2["ticket"] is None
    assert s.failed_attempts == 2
    assert "Attempt 2 of 3" in res2["reply"]

    # Attempt 3: still mismatch -> locks session
    res3 = check_pending_fraud_trigger(s, user_text="Check TXN_1002.")
    assert res3["trigger"] == "fraud_locked"
    assert res3["ticket"] is None
    assert s.failed_attempts == 3
    assert s.locked is True
    assert "locked" in res3["reply"].lower()


def test_fraud_claimed_amount_match_escalates_to_security_fraud():
    """When transactions match the claimed amount, escalate to Security & Fraud."""
    from paylane_api.service import verify_customer
    s = make_session()
    # Turn 1: claim $115
    check_deterministic_triggers("I have unauthorized charges totalling $115 on my account!", s)
    assert s.pending_fraud == {"loss_usd": 115.0}

    # Verify user1
    verify_customer(s, "user1@paylane.com", "4321")

    # Turn 2: provides TXN_1001 ($40) + TXN_1002 ($75) = $115 == $115 (MATCH!)
    res = check_pending_fraud_trigger(
        s,
        user_text="The charges are TXN_1001 and TXN_1002.",
    )
    assert res is not None
    assert res["trigger"] == "fraud"
    assert res["ticket"]["department"] == "Security & Fraud"
    assert res["ticket"]["priority"] == "normal"  # $115 < $500 -> normal
    assert s.handed_off is True


@pytest.mark.parametrize("message", [
    "I did not make this charge.",
    "My card was stolen.",
    "I think my account was hacked.",
])
def test_fraud_keywords(message):
    s = make_session()
    res = check_deterministic_triggers(message, s)
    assert res is not None and res["trigger"] == "fraud_pending_verification"
    assert s.handed_off is False


def test_fraud_card_ending_digits_not_dollar_amount():
    """Card ending in 4242 must not be parsed as a dollar amount."""
    s = make_session()
    res = check_deterministic_triggers("I see an unauthorized charge on card ending 4242.", s)
    assert res is not None and res["trigger"] == "fraud_pending_verification"
    assert s.pending_fraud["loss_usd"] is None


# --- Messages that must NOT trigger ---
@pytest.mark.parametrize("message", [
    "How do you prevent fraud?",
    "Thanks for your courtesy.",
    "Can you help me with my balance?",
])
def test_no_trigger_for_benign_messages(message):
    s = make_session()
    assert check_deterministic_triggers(message, s) is None
    assert s.handed_off is False


# --- Session already handed off: no duplicate trigger ---
def test_no_duplicate_trigger_when_handed_off():
    s = make_session()
    s.handed_off = True
    assert check_deterministic_triggers("My lawyer is on the way.", s) is None


# --- Dollar amount parsing ---
@pytest.mark.parametrize("text, expected", [
    ("Lost $650 total", 650.0),
    ("lost 200 USD", 200.0),
    ("about 500 dollars", 500.0),
    ("TXN_1234 4242 ending in 4242", None),     # plain numbers are not amounts
    ("I see $100 and $200 in charges", 300.0),  # multiple amounts are summed
])
def test_parse_reported_dollar_amounts(text, expected):
    assert parse_reported_dollar_amounts(text) == expected


# --- Frozen Card Trigger Tests ---
def make_frozen_session():
    s = make_session()
    s.verified = True
    s.customer_id = "cust_102"
    s.card_status = "frozen"
    return s


def test_frozen_card_trigger_escalates():
    """Verified customer inquiring about frozen card triggers Security & Fraud high priority."""
    s = make_frozen_session()
    res = check_frozen_card_trigger("Why is my card frozen and can you unfreeze it?", s)
    assert res is not None
    assert res["trigger"] == "frozen_card"
    assert res["ticket"]["department"] == "Security & Fraud"
    assert res["ticket"]["priority"] == "high"
    assert "frozen" in res["reply"].lower()
    assert s.handed_off is True


def test_frozen_card_unrelated_refund_does_not_trigger():
    """An unrelated refund request must not trigger frozen card escalation."""
    s = make_frozen_session()
    res = check_frozen_card_trigger("Please refund TXN_1001.", s)
    assert res is None
    assert s.handed_off is False


# --- Verification retry backstop (3-strike lockout must not depend on the model) ---
RETRY_EMAIL = "user1@paylane.com"
RETRY_HISTORY = [
    f"My email is {RETRY_EMAIL} and card is 9999.",
    "Try card 5555.",
]


def test_retry_after_failure_reuses_earlier_email():
    s = make_session(failed_attempts=1)
    res = check_verification_retry("Try card 5555.", RETRY_HISTORY, s)
    assert res == {"email": RETRY_EMAIL, "card_last4": "5555"}


@pytest.mark.parametrize("text, last4", [
    ("Still me, card is 0000.", "0000"),
    ("Now use card 4321.", "4321"),
    ("last 4 is 1234", "1234"),
    ("it's the one ending in 7777", "7777"),
])
def test_retry_recognises_phrasings(text, last4):
    s = make_session(failed_attempts=2)
    res = check_verification_retry(text, RETRY_HISTORY, s)
    assert res is not None and res["card_last4"] == last4


def test_retry_first_attempt_is_left_to_the_model():
    s = make_session(failed_attempts=0)
    assert check_verification_retry("Try card 5555.", RETRY_HISTORY, s) is None


def test_retry_without_any_email_returns_none():
    s = make_session(failed_attempts=1)
    assert check_verification_retry("Try card 5555.", ["card is 9999", "Try card 5555."], s) is None


def test_retry_skipped_when_verified():
    s = make_session(failed_attempts=1)
    s.verified = True
    assert check_verification_retry("Try card 5555.", RETRY_HISTORY, s) is None


def test_retry_skipped_when_handed_off():
    s = make_session(failed_attempts=1)
    s.handed_off = True
    assert check_verification_retry("Try card 5555.", RETRY_HISTORY, s) is None


def test_retry_skipped_when_locked():
    s = make_session(failed_attempts=3)
    s.locked = True
    assert check_verification_retry("Try card 5555.", RETRY_HISTORY, s) is None


@pytest.mark.parametrize("text", [
    "Please refund TXN_1001.",          # a transaction ID is not a card
    "I was charged $7500 yesterday.",   # an amount is not a card
    "What are your refund rules?",
])
def test_retry_ignores_unrelated_messages(text):
    s = make_session(failed_attempts=1)
    assert check_verification_retry(text, RETRY_HISTORY, s) is None


# --- Refund escalation backstop (ABOVE_LIMIT / TOO_OLD must always reach Billing & Refunds) ---
def _refund_session(txn_id):
    from paylane_api.service import verify_customer, issue_refund
    s = make_session()
    verify_customer(s, "user1@paylane.com", "4321")
    res = issue_refund(s, txn_id)
    s.trace.append({"step": "tool_http_call", "tool": "issue_refund", "response": res})
    return s


@pytest.mark.parametrize("txn_id", ["TXN_1003", "TXN_1004"])  # $75.01, exactly 30 days
def test_refund_over_limit_or_too_old_escalates_to_billing(txn_id):
    s = _refund_session(txn_id)
    res = check_refund_escalation_trigger(s)
    assert res is not None and res["trigger"] == "refund_review"
    assert res["ticket"]["department"] == "Billing & Refunds"
    assert s.handed_off is True
    assert "hour" not in res["reply"].lower() and "day" not in res["reply"].lower()


@pytest.mark.parametrize("txn_id", ["TXN_1001", "TXN_1005", "TXN_1006"])  # ok, pending, refunded
def test_refund_other_outcomes_do_not_escalate(txn_id):
    s = _refund_session(txn_id)
    assert check_refund_escalation_trigger(s) is None
    assert s.handed_off is False


def test_refund_escalation_not_duplicated_after_handoff():
    s = _refund_session("TXN_1003")
    assert check_refund_escalation_trigger(s) is not None
    assert check_refund_escalation_trigger(s) is None