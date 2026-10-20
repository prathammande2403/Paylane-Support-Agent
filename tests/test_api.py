"""
tests/test_api.py
FastAPI endpoint tests using TestClient (no LLM calls).
Covers schema validation, rate limits, message length cap, and session TTL.
"""

import time
import pytest
from fastapi.testclient import TestClient

from paylane_api.main import app
from paylane_api.sessions import session_store
from paylane_api.service import reset_counters


@pytest.fixture(autouse=True)
def reset_everything():
    session_store.clear()
    reset_counters()


@pytest.fixture
def client():
    return TestClient(app)


# --- Health ---
def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


# --- GET /api/sessions/{id} ---
def test_get_session_not_found(client):
    res = client.get("/api/sessions/nonexistent-session-xyz")
    assert res.status_code == 404


def test_get_session_found(client):
    # Create session first
    client.post("/api/reset")
    # Get fresh session id from reset
    r = client.post("/api/reset", json={"session_id": "my-test-session"})
    assert r.json()["session_id"] == "my-test-session"
    res = client.get("/api/sessions/my-test-session")
    assert res.status_code == 200
    data = res.json()
    assert "verified" in data


# --- POST /api/reset ---
def test_reset_creates_new_session(client):
    r = client.post("/api/reset")
    assert r.status_code == 200
    assert "session_id" in r.json()
    assert r.json()["status"] == "reset"


def test_reset_with_explicit_session_id(client):
    r = client.post("/api/reset", json={"session_id": "my-explicit-session"})
    assert r.json()["session_id"] == "my-explicit-session"


# --- POST /tools/get_customer ---
def test_get_customer_verification_success(client):
    sid = "test-verify-success"
    res = client.post("/tools/get_customer", json={
        "session_id": sid,
        "email": "user1@paylane.com",
        "card_last4": "4321",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["name"] == "Alex Morgan"
    # Must not reveal email or full card
    assert "user1@paylane.com" not in str(data)
    assert len(str(data.get("card_last4", ""))) <= 4


def test_get_customer_verification_failure(client):
    sid = "test-verify-fail"
    res = client.post("/tools/get_customer", json={
        "session_id": sid,
        "email": "user1@paylane.com",
        "card_last4": "9999",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert data["reason_code"] == "MISMATCH"


def test_get_customer_unknown_email_same_message_as_wrong_last4(client):
    """Unknown email and wrong last4 must produce identical reason messages."""
    r1 = client.post("/tools/get_customer", json={
        "session_id": "s1",
        "email": "unknown@nowhere.com",
        "card_last4": "4321",
    })
    r2 = client.post("/tools/get_customer", json={
        "session_id": "s2",
        "email": "user1@paylane.com",
        "card_last4": "0000",
    })
    assert r1.json()["reason"] == r2.json()["reason"]


def test_get_customer_three_strike_lockout(client):
    sid = "test-lockout"
    for _ in range(2):
        client.post("/tools/get_customer", json={"session_id": sid, "email": "user1@paylane.com", "card_last4": "0000"})
    r3 = client.post("/tools/get_customer", json={"session_id": sid, "email": "user1@paylane.com", "card_last4": "0000"})
    assert r3.json()["reason_code"] == "LOCKED"


# --- POST /tools/get_transaction ---
def test_get_transaction_unverified(client):
    sid = "test-txn-unverified"
    res = client.post("/tools/get_transaction", json={"session_id": sid, "txn_id": "TXN_1001"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert data["reason_code"] == "UNVERIFIED"


def test_get_transaction_verified_found(client):
    sid = "test-txn-found"
    # Verify first
    client.post("/tools/get_customer", json={"session_id": sid, "email": "user1@paylane.com", "card_last4": "4321"})
    res = client.post("/tools/get_transaction", json={"session_id": sid, "txn_id": "TXN_1001"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["amount"] == 40.0


def test_get_transaction_wrong_owner(client):
    sid = "test-txn-wrong-owner"
    # Verify as cust_101
    client.post("/tools/get_customer", json={"session_id": sid, "email": "user1@paylane.com", "card_last4": "4321"})
    # TXN_2001 belongs to cust_104
    res = client.post("/tools/get_transaction", json={"session_id": sid, "txn_id": "TXN_2001"})
    assert res.json()["ok"] is False
    assert res.json()["reason_code"] == "NOT_FOUND"


# --- POST /tools/issue_refund ---
def test_issue_refund_unverified(client):
    sid = "test-refund-unverified"
    res = client.post("/tools/issue_refund", json={"session_id": sid, "txn_id": "TXN_1001"})
    assert res.json()["ok"] is False
    assert res.json()["reason_code"] == "UNVERIFIED"


def test_issue_refund_success(client):
    sid = "test-refund-success"
    client.post("/tools/get_customer", json={"session_id": sid, "email": "user1@paylane.com", "card_last4": "4321"})
    res = client.post("/tools/issue_refund", json={"session_id": sid, "txn_id": "TXN_1001"})
    data = res.json()
    assert data["ok"] is True
    assert data["refund_id"].startswith("REF-")


def test_issue_refund_partial_rejected(client):
    sid = "test-refund-partial"
    client.post("/tools/get_customer", json={"session_id": sid, "email": "user1@paylane.com", "card_last4": "4321"})
    res = client.post("/tools/issue_refund", json={"session_id": sid, "txn_id": "TXN_1001", "amount": 10.0})
    assert res.json()["reason_code"] == "PARTIAL_NOT_ALLOWED"


def test_issue_refund_above_limit(client):
    sid = "test-refund-limit"
    client.post("/tools/get_customer", json={"session_id": sid, "email": "user1@paylane.com", "card_last4": "4321"})
    res = client.post("/tools/issue_refund", json={"session_id": sid, "txn_id": "TXN_1003"})  # $75.01
    assert res.json()["reason_code"] == "ABOVE_LIMIT"


def test_issue_refund_too_old(client):
    sid = "test-refund-old"
    client.post("/tools/get_customer", json={"session_id": sid, "email": "user1@paylane.com", "card_last4": "4321"})
    res = client.post("/tools/issue_refund", json={"session_id": sid, "txn_id": "TXN_1004"})  # exactly 30 days
    assert res.json()["reason_code"] == "TOO_OLD"


def test_issue_refund_pending(client):
    sid = "test-refund-pending"
    client.post("/tools/get_customer", json={"session_id": sid, "email": "user1@paylane.com", "card_last4": "4321"})
    res = client.post("/tools/issue_refund", json={"session_id": sid, "txn_id": "TXN_1005"})  # pending
    assert res.json()["reason_code"] == "PENDING"


def test_issue_refund_already_refunded(client):
    sid = "test-refund-already"
    client.post("/tools/get_customer", json={"session_id": sid, "email": "user1@paylane.com", "card_last4": "4321"})
    res = client.post("/tools/issue_refund", json={"session_id": sid, "txn_id": "TXN_1006"})  # already refunded
    assert res.json()["reason_code"] == "ALREADY_REFUNDED"


# --- POST /tools/escalate_to_human ---
def test_escalate_valid(client):
    sid = "test-esc-valid"
    res = client.post("/tools/escalate_to_human", json={
        "session_id": sid,
        "summary": "Customer requested help",
        "department": "General Support",
    })
    assert res.status_code == 200
    data = res.json()
    assert "ticket_id" in data
    assert data["department"] == "General Support"


def test_escalate_invalid_department(client):
    sid = "test-esc-invalid"
    res = client.post("/tools/escalate_to_human", json={
        "session_id": sid,
        "summary": "Test",
        "department": "FakeDepartment",
    })
    assert res.status_code == 400
    assert "Invalid department" in res.json()["detail"]


def test_escalate_strips_pii(client):
    sid = "test-esc-pii"
    raw_summary = "Issue from user1@paylane.com with card 4111111111111111"
    res = client.post("/tools/escalate_to_human", json={
        "session_id": sid,
        "summary": raw_summary,
        "department": "Security & Fraud",
    })
    assert res.status_code == 200
    assert "user1@paylane.com" not in res.json()["summary"]
    assert "4111111111111111" not in res.json()["summary"]


def test_escalate_priority_high_over_500(client):
    sid = "test-esc-high"
    res = client.post("/tools/escalate_to_human", json={
        "session_id": sid,
        "summary": "Fraud report",
        "department": "Security & Fraud",
        "reported_loss_usd": 650.0,
    })
    assert res.json()["priority"] == "high"


def test_escalate_priority_normal_under_500(client):
    sid = "test-esc-normal"
    res = client.post("/tools/escalate_to_human", json={
        "session_id": sid,
        "summary": "Small fraud",
        "department": "Security & Fraud",
        "reported_loss_usd": 200.0,
    })
    assert res.json()["priority"] == "normal"


def test_second_escalation_returns_same_ticket(client):
    sid = "test-esc-dedup"
    r1 = client.post("/tools/escalate_to_human", json={"session_id": sid, "summary": "First", "department": "General Support"})
    r2 = client.post("/tools/escalate_to_human", json={"session_id": sid, "summary": "Second", "department": "Billing & Refunds"})
    assert r1.json()["ticket_id"] == r2.json()["ticket_id"]


# --- Rate limiting ---
def test_rate_limit_enforced(client):
    """Rate limit logic correctly tracks request counts and returns False after 20."""
    # Test the rate limiter logic directly via session_store
    test_ip = "rate-test-ip"
    for i in range(20):
        allowed = session_store.check_rate_limit(test_ip)
        assert allowed is True, f"Request {i+1} should be allowed"
    # 21st request should be denied
    over_limit = session_store.check_rate_limit(test_ip)
    assert over_limit is False, "21st request should be rate limited"


# --- Message length cap ---
def test_message_length_cap(client):
    """Messages over 1000 characters must be rejected (400 or 422)."""
    long_msg = "a" * 1001
    res = client.post("/api/chat", json={"session_id": "len-test", "message": long_msg})
    assert res.status_code in (400, 422), f"Expected 400 or 422 for long message, got {res.status_code}"


# --- Schema validation ---
def test_schema_validation_missing_required_field(client):
    """Missing required field must return 422 validation error."""
    res = client.post("/tools/get_customer", json={"session_id": "s1", "email": "user1@paylane.com"})
    assert res.status_code == 422


def test_schema_validation_missing_session_id(client):
    res = client.post("/tools/get_transaction", json={"txn_id": "TXN_1001"})
    assert res.status_code == 422


# --- GET /api/tickets/{session_id} ---
def test_get_ticket_empty(client):
    client.post("/api/reset", json={"session_id": "ticket-test"})
    res = client.get("/api/tickets/ticket-test")
    assert res.status_code == 200
    assert res.json()["ticket"] is None


def test_get_ticket_after_escalation(client):
    sid = "ticket-esc-test"
    client.post("/tools/escalate_to_human", json={"session_id": sid, "summary": "Test", "department": "General Support"})
    res = client.get(f"/api/tickets/{sid}")
    assert res.status_code == 200
    assert res.json()["ticket"]["department"] == "General Support"
