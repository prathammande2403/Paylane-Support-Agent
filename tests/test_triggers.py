"""
tests/test_triggers.py
Tests for deterministic emergency triggers (legal, closure, human, fraud),
the frozen-card trigger, and the verification-retry backstop.
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


# --- Fraud triggers ---
@pytest.mark.parametrize("message, priority, loss", [
    ("There were unauthorized charges totalling $650.", "high", 650.0),
    ("I see an unauthorized charge of $200.", "normal", 200.0),
    ("Someone used my card without my permission!", "normal", None),
])
def test_fraud_priority_and_loss(message, priority, loss):
    """Loss > $500 -> high priority; <= $500 or no amount -> normal."""
    s = make_session()
    res = check_deterministic_triggers(message, s)
    assert res is not None and res["trigger"] == "fraud"
    assert res["ticket"]["department"] == "Security & Fraud"
    assert res["ticket"]["priority"] == priority
    assert res["ticket"]["reported_loss_usd"] == loss


@pytest.mark.parametrize("message", [
    "I did not make this charge.",
    "My card was stolen.",
    "I think my account was hacked.",
])
def test_fraud_keywords(message):
    s = make_session()
    res = check_deterministic_triggers(message, s)
    assert res is not None and res["trigger"] == "fraud"


def test_fraud_card_ending_digits_not_dollar_amount():
    """Card ending in 4242 must not be parsed as a dollar amount."""
    s = make_session()
    res = check_deterministic_triggers("I see an unauthorized charge on card ending 4242.", s)
    assert res is not None and res["trigger"] == "fraud"
    assert res["ticket"]["reported_loss_usd"] is None or res["ticket"]["reported_loss_usd"] < 4242


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
    s = make_session(failed_attempts=3)
    s.handed_off = True
    assert check_verification_retry("Try card 5555.", RETRY_HISTORY, s) is None


@pytest.mark.parametrize("text", [
    "Please refund TXN_1001.",          # a transaction ID is not a card
    "I was charged $7500 yesterday.",   # an amount is not a card
    "What are your refund rules?",
])
def test_retry_ignores_unrelated_messages(text):
    s = make_session(failed_attempts=1)
    assert check_verification_retry(text, RETRY_HISTORY, s) is None