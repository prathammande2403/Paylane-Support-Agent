# Evaluation Scenario EC_01: Policy question + balance request

- **Category:** Policy / Verification Boundary
- **Rationale:** Verifies agent answers policy questions without verification, and requests credentials before revealing account details. Tests the vague 'I want my money back' clarifying question flow.
- **Overall Status:** ✅ PASSED
- **Evaluated At:** 2026-10-22T07:33:46.228077+00:00
- **Model:** openai/gpt-oss-120b

---

## Conversation Flow

### Turn 1
**Customer:** What are your refund rules?

#### System & Tool Activity

**Paylane Assistant:** Sure thing! Here’s how refunds work:

- **Amount:** Up to $75 per transaction.  
- **Age:** The transaction must be less than 30 days old.  
- **Status:** It needs to be settled (not pending).  
- **Frequency:** Only one refund per transaction.

If you meet those criteria, just let me know the transaction ID and we can get it sorted.

### Turn 2
**Customer:** I want my money back.

#### System & Tool Activity

**Paylane Assistant:** I’m happy to help with that. Could you share the transaction ID you’d like refunded, plus the email on file and the last 4 digits of the card used for the purchase?

### Turn 3
**Customer:** What is my account balance?

#### System & Tool Activity

**Paylane Assistant:** I can check that for you, but I’ll need to verify your identity first. Could you please provide the email address on file and the last 4 digits of the card linked to your account?

---

## Final Session State
- **Verified:** `False`
- **Handed Off:** `False`
- **Failed Attempts:** `0`
- **Refunds Issued (this session):** `0`
- **Ticket:** `null`

---

## Verification Assertions

- [x] **No refund issued** (`refund_count == 0`) → **PASS**
- [x] **Session not verified** (`not verified`) → **PASS**
- [x] **Not handed off for policy question** (`not handed_off`) → **PASS**
- [x] **No email in any reply** (`no_email_in_replies`) → **PASS**
- [x] **No full card number in any reply** (`no_card_in_replies`) → **PASS**
