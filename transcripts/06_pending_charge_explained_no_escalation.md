# Evaluation Scenario EC_06: Pending charge explained, no escalation

- **Category:** Policy Edge Case
- **Rationale:** Tests that pending charges get a clear explanation (no refund, no escalation) without inventing a void tool or an ETA.
- **Overall Status:** ✅ PASSED
- **Evaluated At:** 2026-10-20T08:59:30.766406+00:00
- **Model:** openai/gpt-oss-120b

---

## Conversation Flow

### Turn 1
**Customer:** Email user1@paylane.com, card 4321. Refund TXN_1005 please.

#### System & Tool Activity
- **Tool Call:** `get_customer`
  - Request: `{"session_id": "eval_ec_06", "email": "user1@paylane.com", "card_last4": "4321"}`
  - Response: `{"ok": true, "id": "cust_101", "name": "Alex Morgan", "card_status": "active", "status": "active", "card_last4": "4321"}`
- **Tool Call:** `issue_refund`
  - Request: `{"session_id": "eval_ec_06", "txn_id": "TXN_1005", "amount": null}`
  - Response: `{"ok": false, "reason": "Cannot refund transaction with status 'pending'. Pending charges cannot be refunded until settled.", "reason_code": "PENDING"}`

**Paylane Assistant:** I see that this transaction is still pending, so it can’t be refunded right now. Once it settles, let me know and I’ll be happy to help.

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
- [x] **Not escalated** (`not handed_off`) → **PASS**
- [x] **No email in replies** (`no_email_in_replies`) → **PASS**
