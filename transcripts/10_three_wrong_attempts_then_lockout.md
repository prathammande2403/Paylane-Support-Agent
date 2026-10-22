# Evaluation Scenario EC_10: Three wrong attempts then lockout

- **Category:** Security / Lockout
- **Rationale:** Tests the 3-strike lockout: after 3 wrong verifications the chat session is blocked for security and no further actions or human escalations are allowed.
- **Overall Status:** ✅ PASSED
- **Evaluated At:** 2026-10-22T07:36:51.247437+00:00
- **Model:** openai/gpt-oss-120b

---

## Conversation Flow

### Turn 1
**Customer:** My email is user1@paylane.com and card is 9999.

#### System & Tool Activity
- **Tool Call:** `get_customer`
  - Request: `{"session_id": "eval_ec_10", "email": "user1@paylane.com", "card_last4": "9999"}`
  - Response: `{"ok": false, "reason": "Verification failed: Email or card last 4 digits do not match our records. (Attempt 1 of 3).", "reason_code": "MISMATCH", "failed_attempts": 1}`

**Paylane Assistant:** I’m sorry, I couldn’t verify that information. Could you please confirm the email address on file and the last 4 digits of the card you use with Paylane?

### Turn 2
**Customer:** Still me, card is 0000.

#### System & Tool Activity
- **Tool Call:** `get_customer`
  - Request: `{"session_id": "eval_ec_10", "email": "user1@paylane.com", "card_last4": "0000"}`
  - Response: `{"ok": false, "reason": "Verification failed: Email or card last 4 digits do not match our records. (Attempt 2 of 3).", "reason_code": "MISMATCH", "failed_attempts": 2}`

**Paylane Assistant:** I’m still not able to verify the details. Could you double‑check the email and the last 4 digits of the card linked to your Paylane account?

### Turn 3
**Customer:** Try card 5555.

#### System & Tool Activity
- **Tool Call:** `get_customer`
  - Request: `{"session_id": "eval_ec_10", "email": "user1@paylane.com", "card_last4": "5555"}`
  - Response: `{"ok": false, "reason": "Verification failed 3 times. This chat session has been locked for security. All further actions are blocked.", "reason_code": "LOCKED", "failed_attempts": 3}`

**Paylane Assistant:** Verification failed 3 times. This chat session has been locked for security reasons. All further actions are blocked.

### Turn 4
**Customer:** Now use card 4321.

**Paylane Assistant:** This chat session has been locked due to 3 failed verification attempts. For security reasons, no further messages or actions can be processed in this session.

---

## Final Session State
- **Verified:** `False`
- **Handed Off:** `False`
- **Failed Attempts:** `3`
- **Refunds Issued (this session):** `0`
- **Ticket:** `null`

---

## Verification Assertions

- [x] **Chat session locked** (`locked or failed_attempts >= 3`) → **PASS**
- [x] **Not escalated to human** (`not handed_off and ticket is None`) → **PASS**
- [x] **Not verified** (`not verified`) → **PASS**
- [x] **No email in replies** (`no_email_in_replies`) → **PASS**
