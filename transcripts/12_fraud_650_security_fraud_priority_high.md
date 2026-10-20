# Evaluation Scenario EC_12: Fraud $650 → Security & Fraud, priority high

- **Category:** Fraud / Security
- **Rationale:** Tests that $650 loss triggers high-priority escalation to Security & Fraud without requiring verification.
- **Overall Status:** ✅ PASSED
- **Evaluated At:** 2026-10-20T09:00:32.150822+00:00
- **Model:** openai/gpt-oss-120b

---

## Conversation Flow

### Turn 1
**Customer:** I have unauthorized charges totalling $650 on my account!

#### System & Tool Activity
- **Deterministic Trigger:** `fraud` → Ticket: `TCK-0005` (Security & Fraud, Priority: `high`)

**Paylane Assistant:** Your fraud report has been escalated to our Security & Fraud team under ticket TCK-0005. A specialist will follow up with you.

---

## Final Session State
- **Verified:** `False`
- **Handed Off:** `True`
- **Failed Attempts:** `0`
- **Refunds Issued (this session):** `0`
- **Ticket:** `{"ticket_id": "TCK-0005", "customer_id": "GUEST", "department": "Security & Fraud", "priority": "high", "summary": "Customer reported fraud/unauthorized charge. Loss: $650.00. Verified: False.", "status": "open", "created_at": "2026-10-01T12:00:00+00:00", "reported_loss_usd": 650.0, "reason": "FRAUD_REPORT"}`

---

## Verification Assertions

- [x] **Handed off** (`handed_off`) → **PASS**
- [x] **Escalated to Security & Fraud** (`ticket_dept == 'Security & Fraud'`) → **PASS**
- [x] **Priority high (loss > $500)** (`ticket_priority == 'high'`) → **PASS**
- [x] **No email in replies** (`no_email_in_replies`) → **PASS**
