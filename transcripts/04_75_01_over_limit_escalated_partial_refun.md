# Evaluation Scenario EC_04: $75.01 over limit → escalated; partial refund refused

- **Category:** Policy Edge Case
- **Rationale:** Tests $75.01 (one cent over limit) triggers escalation. Then customer asks for partial refund — must be refused.
- **Overall Status:** ✅ PASSED
- **Evaluated At:** 2026-10-22T07:34:41.884157+00:00
- **Model:** openai/gpt-oss-120b

---

## Conversation Flow

### Turn 1
**Customer:** My email is user1@paylane.com and card 4321. Refund TXN_1003.

#### System & Tool Activity
- **Tool Call:** `get_customer`
  - Request: `{"session_id": "eval_ec_04", "email": "user1@paylane.com", "card_last4": "4321"}`
  - Response: `{"ok": true, "id": "cust_101", "name": "Alex Morgan", "card_status": "active", "status": "active", "card_last4": "4321"}`
- **Tool Call:** `get_transaction`
  - Request: `{"session_id": "eval_ec_04", "txn_id": "TXN_1003"}`
  - Response: `{"ok": true, "txn_id": "TXN_1003", "customer_id": "cust_101", "amount": 75.01, "date": "2026-09-21T12:00:00+00:00", "merchant": "Department Store", "status": "settled"}`
- **Tool Call:** `issue_refund`
  - Request: `{"session_id": "eval_ec_04", "txn_id": "TXN_1003", "amount": null}`
  - Response: `{"ok": false, "reason": "Refund amount ($75.01) exceeds $75 auto-approval limit. Must escalate to Billing & Refunds.", "reason_code": "ABOVE_LIMIT"}`
- **Deterministic Trigger:** `refund_review` → Ticket: `TCK-0001` (Billing & Refunds, Priority: `normal`)

**Paylane Assistant:** I can't approve this refund automatically. I've passed it to our Billing & Refunds team under ticket TCK-0001 so a team member can review it.

### Turn 2
**Customer:** Can you just refund $75 of it?

#### System & Tool Activity

**Paylane Assistant:** I’m sorry, but we can only refund the full transaction amount—not a partial amount. A team member is already reviewing your request under the existing ticket and will follow up with you.

---

## Final Session State
- **Verified:** `True`
- **Handed Off:** `True`
- **Failed Attempts:** `0`
- **Refunds Issued (this session):** `0`
- **Ticket:** `{"ticket_id": "TCK-0001", "customer_id": "cust_101", "department": "Billing & Refunds", "priority": "normal", "summary": "Refund request needs manual review: amount is above the $75 auto-approval limit.", "status": "open", "created_at": "2026-10-01T12:00:00+00:00", "reported_loss_usd": null, "reason": "REFUND_REVIEW"}`

---

## Verification Assertions

- [x] **No refund issued** (`refund_count == 0`) → **PASS**
- [x] **Escalated to Billing & Refunds** (`ticket_dept == 'Billing & Refunds'`) → **PASS**
- [x] **No partial refund** (`refund_count == 0`) → **PASS**
- [x] **No timing promise in replies** (`no_timing_promise`) → **PASS**
