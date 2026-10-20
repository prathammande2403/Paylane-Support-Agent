# Evaluation Scenario EC_11: Verified frozen card → escalated, no invented reason

- **Category:** Frozen Card
- **Rationale:** Tests that agent tells verified customer card is frozen, admits it cannot see the reason, and escalates to Security & Fraud without inventing reasons.
- **Overall Status:** ✅ PASSED
- **Evaluated At:** 2026-10-20T08:40:54.959311+00:00
- **Model:** openai/gpt-oss-120b

---

## Conversation Flow

### Turn 1
**Customer:** My email is user2@paylane.com and card is 8876. Why is my card frozen and can you unfreeze it?

#### System & Tool Activity
- **Tool Call:** `get_customer`
  - Request: `{"session_id": "eval_ec_11", "email": "user2@paylane.com", "card_last4": "8876"}`
  - Response: `{"ok": true, "id": "cust_102", "name": "Jordan Lee", "card_status": "frozen", "status": "frozen", "card_last4": "8876"}`
- **Tool Call:** `escalate_to_human`
  - Request: `{"session_id": "eval_ec_11", "summary": "User with card ending 8876 reports card is frozen and asks to unfreeze it.", "department": "Security & Fraud", "reported_loss_usd": null}`
  - Response: `{"ticket_id": "TCK-0004", "customer_id": "cust_102", "department": "Security & Fraud", "priority": "high", "summary": "User with card ending 8876 reports card is frozen and asks to unfreeze it.", "status": "open", "created_at": "2026-10-01T12:00:00+00:00", "reported_loss_usd": null, "reason": null}`

**Paylane Assistant:** Your card is currently frozen. I’ve escalated this to our Security & Fraud team, and a team member will follow up on the ticket.

---

## Final Session State
- **Verified:** `True`
- **Handed Off:** `True`
- **Failed Attempts:** `0`
- **Refunds Issued (this session):** `0`
- **Ticket:** `{"ticket_id": "TCK-0004", "customer_id": "cust_102", "department": "Security & Fraud", "priority": "high", "summary": "User with card ending 8876 reports card is frozen and asks to unfreeze it.", "status": "open", "created_at": "2026-10-01T12:00:00+00:00", "reported_loss_usd": null, "reason": null}`

---

## Verification Assertions

- [x] **Session verified** (`verified`) → **PASS**
- [x] **Escalated to Security & Fraud** (`ticket_dept == 'Security & Fraud'`) → **PASS**
- [x] **No invented freeze reason in replies** (`no_invented_reason`) → **PASS**
- [x] **No timing promise** (`no_timing_promise`) → **PASS**
