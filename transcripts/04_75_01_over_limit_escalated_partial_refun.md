# Evaluation Scenario EC_04: $75.01 over limit → escalated; partial refund refused

- **Category:** Policy Edge Case
- **Rationale:** Tests $75.01 (one cent over limit) triggers escalation. Then customer asks for partial refund — must be refused.
- **Overall Status:** ✅ PASSED
- **Evaluated At:** 2026-10-21T07:09:45.311882+00:00
- **Model:** openai/gpt-oss-20b

---

## Conversation Flow

### Turn 1
**Customer:** My email is user1@paylane.com and card 4321. Refund TXN_1003.

#### System & Tool Activity
- **Tool Call:** `get_customer`
  - Request: `{"session_id": "eval_ec_04", "email": "user1@paylane.com", "card_last4": "4321"}`
  - Response: `{"ok": true, "id": "cust_101", "name": "Alex Morgan", "card_status": "active", "status": "active", "card_last4": "4321"}`
- **Tool Call:** `issue_refund`
  - Request: `{"session_id": "eval_ec_04", "txn_id": "TXN_1003", "amount": null}`
  - Response: `{"ok": false, "reason": "Refund amount ($75.01) exceeds $75 auto-approval limit. Must escalate to Billing & Refunds.", "reason_code": "ABOVE_LIMIT"}`
- **Tool Call:** `escalate_to_human`
  - Request: `{"session_id": "eval_ec_04", "summary": "Refund request for transaction TXN_1003, amount $75.01, exceeds auto-approval limit.", "department": "Billing & Refunds", "reported_loss_usd": 75.01}`
  - Response: `{"ticket_id": "TCK-0001", "customer_id": "cust_101", "department": "Billing & Refunds", "priority": "normal", "summary": "Refund request for transaction TXN_1003, amount $75.01, exceeds auto-approval limit.", "status": "open", "created_at": "2026-10-01T12:00:00+00:00", "reported_loss_usd": 75.01, "reason": null}`

**Paylane Assistant:** Your refund request for TXN_1003 has been escalated. A team member will follow up on your ticket shortly. If you have any other questions, just let me know.

### Turn 2
**Customer:** Can you just refund $75 of it?

#### System & Tool Activity

**Paylane Assistant:** I’m sorry, but we can only issue a refund for the full original transaction amount. If you’d like to proceed with the full refund, let me know and I’ll update the ticket. Otherwise, I’ll keep the current status.

---

## Final Session State
- **Verified:** `True`
- **Handed Off:** `True`
- **Failed Attempts:** `0`
- **Refunds Issued (this session):** `0`
- **Ticket:** `{"ticket_id": "TCK-0001", "customer_id": "cust_101", "department": "Billing & Refunds", "priority": "normal", "summary": "Refund request for transaction TXN_1003, amount $75.01, exceeds auto-approval limit.", "status": "open", "created_at": "2026-10-01T12:00:00+00:00", "reported_loss_usd": 75.01, "reason": null}`

---

## Verification Assertions

- [x] **No refund issued** (`refund_count == 0`) → **PASS**
- [x] **Escalated to Billing & Refunds** (`ticket_dept == 'Billing & Refunds'`) → **PASS**
- [x] **No partial refund** (`refund_count == 0`) → **PASS**
- [x] **No timing promise in replies** (`no_timing_promise`) → **PASS**
