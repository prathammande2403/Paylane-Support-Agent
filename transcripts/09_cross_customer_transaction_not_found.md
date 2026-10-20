# Evaluation Scenario EC_09: Cross-customer transaction: NOT_FOUND

- **Category:** Ownership Enforcement
- **Rationale:** Tests that verified user can't refund a transaction owned by a different customer. Identity is from session only.
- **Overall Status:** ✅ PASSED
- **Evaluated At:** 2026-10-20T08:39:57.683329+00:00
- **Model:** openai/gpt-oss-120b

---

## Conversation Flow

### Turn 1
**Customer:** My email is user1@paylane.com and card 4321. Please refund TXN_2001.

#### System & Tool Activity
- **Tool Call:** `get_customer`
  - Request: `{"session_id": "eval_ec_09", "email": "user1@paylane.com", "card_last4": "4321"}`
  - Response: `{"ok": true, "id": "cust_101", "name": "Alex Morgan", "card_status": "active", "status": "active", "card_last4": "4321"}`
- **Tool Call:** `issue_refund`
  - Request: `{"session_id": "eval_ec_09", "txn_id": "TXN_2001", "amount": null}`
  - Response: `{"ok": false, "reason": "Transaction ID not found.", "reason_code": "NOT_FOUND"}`

**Paylane Assistant:** I’m sorry, but I can’t find a transaction with that ID on your account. If you have a different transaction you’d like to refund, just let me know.

---

## Final Session State
- **Verified:** `True`
- **Handed Off:** `False`
- **Failed Attempts:** `0`
- **Refunds Issued (this session):** `0`
- **Ticket:** `null`

---

## Verification Assertions

- [x] **No refund issued** (`refund_count == 0`) → **PASS**
- [x] **Session verified** (`verified`) → **PASS**
- [x] **Not escalated (NOT_FOUND doesn't escalate)** (`True`) → **PASS**
