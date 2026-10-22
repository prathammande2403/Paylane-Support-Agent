# Evaluation Scenario EC_05: Exactly 30 days old → escalated

- **Category:** Boundary Condition
- **Rationale:** Tests that exactly 30 days (not less-than) triggers escalation. The timedelta comparison must use >= 30 days, not just .days check.
- **Overall Status:** ✅ PASSED
- **Evaluated At:** 2026-10-22T07:35:02.021984+00:00
- **Model:** openai/gpt-oss-120b

---

## Conversation Flow

### Turn 1
**Customer:** user1@paylane.com, card 4321. Refund TXN_1004 please.

#### System & Tool Activity
- **Tool Call:** `get_customer`
  - Request: `{"session_id": "eval_ec_05", "email": "user1@paylane.com", "card_last4": "4321"}`
  - Response: `{"ok": true, "id": "cust_101", "name": "Alex Morgan", "card_status": "active", "status": "active", "card_last4": "4321"}`
- **Tool Call:** `get_transaction`
  - Request: `{"session_id": "eval_ec_05", "txn_id": "TXN_1004"}`
  - Response: `{"ok": true, "txn_id": "TXN_1004", "customer_id": "cust_101", "amount": 30.0, "date": "2026-09-01T12:00:00+00:00", "merchant": "Bookstore", "status": "settled"}`
- **Tool Call:** `issue_refund`
  - Request: `{"session_id": "eval_ec_05", "txn_id": "TXN_1004", "amount": null}`
  - Response: `{"ok": false, "reason": "Transaction is 30 days old. Exceeds 30-day refund window. Must escalate to Billing & Refunds.", "reason_code": "TOO_OLD"}`
- **Deterministic Trigger:** `refund_review` → Ticket: `TCK-0002` (Billing & Refunds, Priority: `normal`)

**Paylane Assistant:** I can't approve this refund automatically. I've passed it to our Billing & Refunds team under ticket TCK-0002 so a team member can review it.

---

## Final Session State
- **Verified:** `True`
- **Handed Off:** `True`
- **Failed Attempts:** `0`
- **Refunds Issued (this session):** `0`
- **Ticket:** `{"ticket_id": "TCK-0002", "customer_id": "cust_101", "department": "Billing & Refunds", "priority": "normal", "summary": "Refund request needs manual review: transaction is outside the 30-day refund window.", "status": "open", "created_at": "2026-10-01T12:00:00+00:00", "reported_loss_usd": null, "reason": "REFUND_REVIEW"}`

---

## Verification Assertions

- [x] **No refund issued** (`refund_count == 0`) → **PASS**
- [x] **Escalated to Billing & Refunds** (`ticket_dept == 'Billing & Refunds'`) → **PASS**
- [x] **No timing promise** (`no_timing_promise`) → **PASS**
