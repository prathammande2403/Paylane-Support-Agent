"""
tests/test_triggers.py
Tests for deterministic emergency triggers (legal, closure, human, fraud).
No LLM required.
"""

import pytest
from paylane_api.service import SessionState, reset_counters
from data.mock_db import get_fresh_db
from agent.triggers import (
    check_deterministic_triggers,
    parse_reported_dollar_amounts,
    check_frozen_card_trigger,
)


@pytest.fixture(autouse=True)
def setup():
    reset_counters()


def make_session():
    return SessionState(session_id="trigger-test", db=get_fresh_db())


# --- Legal triggers ---
def test_legal_keyword_lawyer():
    s = make_session()
    res = check_deterministic_triggers("I have a lawyer and will sue you.", s)
    assert res is not None
    assert res["trigger"] == "legal"
    assert res["ticket"]["department"] == "Legal & Compliance"
    assert s.handed_off is True


def test_legal_keyword_lawsuit():
    s = make_session()
    res = check_deterministic_triggers("I'm filing a lawsuit against Paylane.", s)
    assert res is not None and res["trigger"] == "legal"


def test_legal_keyword_attorney():
    s = make_session()
    res = check_deterministic_triggers("My attorney will contact you shortly.", s)
    assert res is not None and res["trigger"] == "legal"


def test_legal_keyword_regulator():
    s = make_session()
    res = check_deterministic_triggers("I'll report this to the regulator.", s)
    assert res is not None and res["trigger"] == "legal"


def test_legal_keyword_cfpb():
    s = make_session()
    res = check_deterministic_triggers("I'm filing a CFPB complaint.", s)
    assert res is not None and res["trigger"] == "legal"


def test_legal_keyword_court():
    s = make_session()
    res = check_deterministic_triggers("This matter will be decided in court.", s)
    assert res is not None and res["trigger"] == "legal"


def test_legal_no_verification_needed():
    """Legal handoff must not require verification."""
    s = make_session()
    assert s.verified is False
    res = check_deterministic_triggers("My solicitor will be in touch.", s)
    assert res is not None
    assert res["trigger"] == "legal"
    # Ticket exists without verification
    assert res["ticket"]["customer_id"] == "GUEST"


# --- Account closure triggers ---
def test_closure_close_my_account():
    s = make_session()
    res = check_deterministic_triggers("I want to close my account.", s)
    assert res is not None and res["trigger"] == "account_closure"
    assert res["ticket"]["department"] == "Account Operations"


def test_closure_delete_account():
    s = make_session()
    res = check_deterministic_triggers("Please delete my account immediately.", s)
    assert res is not None and res["trigger"] == "account_closure"


def test_closure_cancel_account():
    s = make_session()
    res = check_deterministic_triggers("I'd like to cancel my Paylane account.", s)
    assert res is not None and res["trigger"] == "account_closure"


def test_closure_no_verification_needed():
    """Account closure must not require verification."""
    s = make_session()
    res = check_deterministic_triggers("Close my account.", s)
    assert res is not None
    assert s.handed_off is True


# --- Explicit human triggers ---
def test_human_speak_to_someone():
    s = make_session()
    res = check_deterministic_triggers("I want to speak to someone.", s)
    assert res is not None and res["trigger"] == "explicit_human"
    assert res["ticket"]["department"] == "General Support"


def test_human_real_person():
    s = make_session()
    res = check_deterministic_triggers("I need to talk to a real person.", s)
    assert res is not None and res["trigger"] == "explicit_human"


def test_human_representative():
    s = make_session()
    res = check_deterministic_triggers("Connect me to a human representative.", s)
    assert res is not None and res["trigger"] == "explicit_human"


def test_human_dont_want_to_talk_to_ai():
    s = make_session()
    res = check_deterministic_triggers("I don't want to talk to an AI.", s)
    assert res is not None and res["trigger"] == "explicit_human"


def test_human_no_verification_needed():
    """Human request must not require verification."""
    s = make_session()
    res = check_deterministic_triggers("I want a human agent.", s)
    assert res is not None
    assert s.handed_off is True


# --- Fraud triggers ---
def test_fraud_high_priority_650():
    """Loss > $500 must produce high priority."""
    s = make_session()
    res = check_deterministic_triggers("There were unauthorized charges totalling $650.", s)
    assert res is not None and res["trigger"] == "fraud"
    assert res["ticket"]["department"] == "Security & Fraud"
    assert res["ticket"]["priority"] == "high"
    assert res["ticket"]["reported_loss_usd"] == 650.0


def test_fraud_normal_priority_200():
    """Loss <= $500 must produce normal priority."""
    s = make_session()
    res = check_deterministic_triggers("I see an unauthorized charge of $200.", s)
    assert res is not None and res["trigger"] == "fraud"
    assert res["ticket"]["priority"] == "normal"


def test_fraud_normal_priority_no_amount():
    """No dollar amount must produce normal priority."""
    s = make_session()
    res = check_deterministic_triggers("Someone used my card without my permission!", s)
    assert res is not None and res["trigger"] == "fraud"
    assert res["ticket"]["priority"] == "normal"
    assert res["ticket"]["reported_loss_usd"] is None


def test_fraud_how_do_you_prevent_fraud_does_NOT_trigger():
    """Generic fraud question must NOT trigger fraud escalation."""
    s = make_session()
    res = check_deterministic_triggers("How do you prevent fraud?", s)
    assert res is None


def test_fraud_card_ending_digits_not_dollar_amount():
    """Card ending in 4242 must not be parsed as a dollar amount."""
    s = make_session()
    res = check_deterministic_triggers("I see an unauthorized charge on card ending 4242.", s)
    assert res is not None and res["trigger"] == "fraud"
    # 4242 must not be treated as dollar amount
    assert res["ticket"]["reported_loss_usd"] is None or res["ticket"]["reported_loss_usd"] < 4242


def test_fraud_did_not_make():
    s = make_session()
    res = check_deterministic_triggers("I did not make this charge.", s)
    assert res is not None and res["trigger"] == "fraud"


def test_fraud_stolen():
    s = make_session()
    res = check_deterministic_triggers("My card was stolen.", s)
    assert res is not None and res["trigger"] == "fraud"


def test_fraud_hacked():
    s = make_session()
    res = check_deterministic_triggers("I think my account was hacked.", s)
    assert res is not None and res["trigger"] == "fraud"


# --- Session already handed off: no duplicate trigger ---
def test_no_duplicate_trigger_when_handed_off():
    """If session is already handed off, triggers must be skipped."""
    s = make_session()
    s.handed_off = True
    res = check_deterministic_triggers("My lawyer is on the way.", s)
    assert res is None  # No new ticket


# --- Dollar amount parsing ---
def test_dollar_sign_parsed():
    assert parse_reported_dollar_amounts("Lost $650 total") == 650.0


def test_usd_suffix_parsed():
    assert parse_reported_dollar_amounts("lost 200 USD") == 200.0


def test_dollars_word_parsed():
    assert parse_reported_dollar_amounts("about 500 dollars") == 500.0


def test_plain_number_not_parsed():
    """Plain number without $ / USD / dollars context must not be parsed."""
    result = parse_reported_dollar_amounts("TXN_1234 4242 ending in 4242")
    assert result is None


def test_multiple_amounts_summed():
    """Multiple $ amounts in same message should be summed."""
    result = parse_reported_dollar_amounts("I see $100 and $200 in charges")
    assert result == 300.0


# --- Frozen Card Trigger Tests ---
def test_frozen_card_trigger_escalates():
    """Verified customer inquiring about frozen card triggers Security & Fraud high priority."""
    s = make_session()
    s.verified = True
    s.customer_id = "cust_102"
    s.card_status = "frozen"

    res = check_frozen_card_trigger("Why is my card frozen and can you unfreeze it?", s)
    assert res is not None
    assert res["trigger"] == "frozen_card"
    assert res["ticket"]["department"] == "Security & Fraud"
    assert res["ticket"]["priority"] == "high"
    assert "frozen" in res["reply"].lower()
    assert s.handed_off is True


def test_frozen_card_unrelated_refund_does_not_trigger():
    """A verified customer with a frozen card requesting an unrelated refund must not trigger frozen card escalation."""
    s = make_session()
    s.verified = True
    s.customer_id = "cust_102"
    s.card_status = "frozen"

    res = check_frozen_card_trigger("Please refund TXN_1001.", s)
    assert res is None
    assert s.handed_off is False
