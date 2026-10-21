# Evaluation Scenario EC_05: Exactly 30 days old → escalated

- **Category:** Boundary Condition
- **Rationale:** Tests that exactly 30 days (not less-than) triggers escalation. The timedelta comparison must use >= 30 days, not just .days check.
- **Overall Status:** ✅ PASSED
- **Evaluated At:** 2026-10-21T07:10:27.296768+00:00
- **Model:** openai/gpt-oss-20b

---

## Conversation Flow

### Turn 1
**Customer:** user1@paylane.com, card 4321. Refund TXN_1004 please.

#### System & Tool Activity
- **Tool Call:** `get_customer`
  - Request: `{"session_id": "eval_ec_05", "email": "user1@paylane.com", "card_last4": "4321"}`
  - Response: `{"ok": true, "id": "cust_101", "name": "Alex Morgan", "card_status": "active", "status": "active", "card_last4": "4321"}`
- **Tool Call:** `issue_refund`
  - Request: `{"session_id": "eval_ec_05", "txn_id": "TXN_1004", "amount": null}`
  - Response: `{"ok": false, "reason": "Transaction is 30 days old. Exceeds 30-day refund window. Must escalate to Billing & Refunds.", "reason_code": "TOO_OLD"}`
- **Tool Call:** `escalate_to_human`
  - Request: `{"session_id": "eval_ec_05", "summary": "Refund request for transaction TXN_1004 is too old (30 days). Escalated to Billing & Refunds.", "department": "Billing & Refunds", "reported_loss_usd": null}`
  - Response: `{"ticket_id": "TCK-0002", "customer_id": "cust_101", "department": "Billing & Refunds", "priority": "normal", "summary": "Refund request for transaction TXN_1004 is too old (30 days). Escalated to Billing & Refunds.", "status": "open", "created_at": "2026-10-01T12:00:00+00:00", "reported_loss_usd": null, "reason": null}`

**Paylane Assistant:** I’ve escalated your refund request to the Billing & Refunds team. A team member will follow up on your ticket shortly. If there’s anything else you need, just let me know.

---

## Final Session State
- **Verified:** `True`
- **Handed Off:** `True`
- **Failed Attempts:** `0`
- **Refunds Issued (this session):** `0`
- **Ticket:** `{"ticket_id": "TCK-0002", "customer_id": "cust_101", "department": "Billing & Refunds", "priority": "normal", "summary": "Refund request for transaction TXN_1004 is too old (30 days). Escalated to Billing & Refunds.", "status": "open", "created_at": "2026-10-01T12:00:00+00:00", "reported_loss_usd": null, "reason": null}`

---

## Verification Assertions

- [x] **No refund issued** (`refund_count == 0`) → **PASS**
- [x] **Escalated to Billing & Refunds** (`ticket_dept == 'Billing & Refunds'`) → **PASS**
- [x] **No timing promise** (`no_timing_promise`) → **PASS**
