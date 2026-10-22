# Evaluation Scenario EC_12: Fraud $650 claimed but DB shows only $115 → amount mismatch pauses escalation, prompts retry

- **Category:** Fraud / Security
- **Rationale:** Tests that when claimed loss ($650) does not match verified transactions ($115), the agent does NOT escalate to human; it pauses, surfaces the discrepancy, and prompts the customer to verify details (Attempt 1 of 3).
- **Overall Status:** ✅ PASSED
- **Evaluated At:** 2026-10-22T07:37:09.990443+00:00
- **Model:** openai/gpt-oss-120b

---

## Conversation Flow

### Turn 1
**Customer:** I have unauthorized charges totalling $650 on my account!

#### System & Tool Activity

**Paylane Assistant:** I'm sorry to hear about this unauthorized charge. To protect your account, I must verify your identity first. Please provide your account email address and the last 4 digits of your card.

### Turn 2
**Customer:** My email is user1@paylane.com, card ending 4321. The charges are TXN_1001 and TXN_1002.

#### System & Tool Activity
- **Tool Call:** `get_customer`
  - Request: `{"session_id": "eval_ec_12", "email": "user1@paylane.com", "card_last4": "4321"}`
  - Response: `{"ok": true, "id": "cust_101", "name": "Alex Morgan", "card_status": "active", "status": "active", "card_last4": "4321"}`
- **Deterministic Trigger:** `fraud_mismatch_retry` → no ticket yet (waiting for identity verification)

**Paylane Assistant:** The transactions found on your account (TXN_1001 ($40.00), TXN_1002 ($75.00) — total: $115.00) do not match the reported amount of $650.00. Please re-check and provide the correct transaction ID(s) or account credentials. (Attempt 1 of 3).

---

## Final Session State
- **Verified:** `True`
- **Handed Off:** `False`
- **Failed Attempts:** `1`
- **Refunds Issued (this session):** `0`
- **Ticket:** `null`

---

## Verification Assertions

- [x] **Customer verified before fraud check** (`verified`) → **PASS**
- [x] **Not escalated to human on amount mismatch** (`not handed_off and ticket is None`) → **PASS**
- [x] **Reply mentions verified dollar amount and discrepancy** (`amount_in_reply`) → **PASS**
- [x] **No email in replies** (`no_email_in_replies`) → **PASS**
