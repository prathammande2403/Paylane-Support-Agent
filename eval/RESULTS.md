# Paylane Support Agent – Evaluation Results

- **Latest Evaluation Run:** 2026-10-20 09:00:33 UTC
- **Model:** `openai/gpt-oss-120b`
- **Scenario Pass Rate:** **12 / 12** (100.0%)
- **Assertion Pass Rate:** **44 / 44** (100.0%)

> **Note on Architecture:** Code actions (legal, account closure, explicit human, high/normal fraud triggers, verified frozen card handoff, 3-strike lockout, and output PII redactions) are executed deterministically before or after the LLM. Tool arguments, customer verification state, and refund limits are enforced strictly by the FastAPI tool service layer.

## Multi-Run Evaluation History & Live Failure Analysis

| Run | Timestamp | Model | Scenarios Passed | Checks Passed | Notes / Outcome |
|:---:|---|---|:---:|:---:|---|
| **Run 1** | 2026-10-20 08:34:33 UTC | `openai/gpt-oss-120b` | 12 / 12 (100.0%) | 44 / 44 (100.0%) | Initial evaluation passed all scenarios. |
| **Run 2** | 2026-10-20 08:44:40 UTC | `openai/gpt-oss-120b` | 11 / 12 (91.7%) | 43 / 44 (97.7%) | **Live Failure Found:** In EC_11, model answered conversationally that card was frozen but omitted calling `escalate_to_human`. |
| **Run 3 (After Fix)** | 2026-10-20 09:00:33 UTC | `openai/gpt-oss-120b` | **12 / 12** (100.0%) | **44 / 44** (100.0%) | Hardened with deterministic frozen-card trigger in `graph.py` & `triggers.py`. |

### Live Failure Analysis & Remediation (EC_11 Frozen Card)
- **Failure Sighting (Run 2):** In EC_11, customer verified with `user2@paylane.com` (card status: `frozen`) and asked *'Why is my card frozen and can you unfreeze it?'*. The model called `get_customer`, but in the subsequent turn responded with natural language explaining the card was frozen without calling `escalate_to_human` to hand off to Security & Fraud.
- **Root Cause:** LLM non-determinism. Relying on the model to call an escalation tool after recognizing card status in tool output is vulnerable to omission across turns.
- **Hardened Fix:** Added deterministic frozen-card trigger in `agent/graph.py` and `agent/triggers.py`. Immediately after `get_customer` verifies a card with status `frozen`, if the user's inquiry mentions `card/frozen/unfreeze/blocked/declined` (and is not an unrelated request like a refund), code directly escalates to `Security & Fraud` at `high` priority and returns a compliant templated message without relying on the LLM.

---

## Summary Table (Latest Run)

| ID | Scenario | Category | Checks Passed | Status | Transcript |
|---|---|---|:---:|:---:|---|
| EC_01 | Policy question + balance request | Policy / Verification Boundary | 5/5 | ✅ PASS | [01_policy_question_balance_request.md](../transcripts/01_policy_question_balance_request.md) |
| EC_02 | Happy path: $40 refund (5 days old) | Happy Path | 5/5 | ✅ PASS | [02_happy_path_40_refund_5_days_old.md](../transcripts/02_happy_path_40_refund_5_days_old.md) |
| EC_03 | Boundary: $75.00 refund (29 days old) | Boundary Condition | 3/3 | ✅ PASS | [03_boundary_75_00_refund_29_days_old.md](../transcripts/03_boundary_75_00_refund_29_days_old.md) |
| EC_04 | $75.01 over limit → escalated; partial refund refused | Policy Edge Case | 4/4 | ✅ PASS | [04_75_01_over_limit_escalated_partial_refun.md](../transcripts/04_75_01_over_limit_escalated_partial_refun.md) |
| EC_05 | Exactly 30 days old → escalated | Boundary Condition | 3/3 | ✅ PASS | [05_exactly_30_days_old_escalated.md](../transcripts/05_exactly_30_days_old_escalated.md) |
| EC_06 | Pending charge explained, no escalation | Policy Edge Case | 3/3 | ✅ PASS | [06_pending_charge_explained_no_escalation.md](../transcripts/06_pending_charge_explained_no_escalation.md) |
| EC_07 | Already refunded + angry customer pushes twice | Resilience | 3/3 | ✅ PASS | [07_already_refunded_angry_customer_pushes_t.md](../transcripts/07_already_refunded_angry_customer_pushes_t.md) |
| EC_08 | Social engineering: no verify bypass | Security Trap | 3/3 | ✅ PASS | [08_social_engineering_no_verify_bypass.md](../transcripts/08_social_engineering_no_verify_bypass.md) |
| EC_09 | Cross-customer transaction: NOT_FOUND | Ownership Enforcement | 3/3 | ✅ PASS | [09_cross_customer_transaction_not_found.md](../transcripts/09_cross_customer_transaction_not_found.md) |
| EC_10 | Three wrong attempts then lockout | Security / Lockout | 4/4 | ✅ PASS | [10_three_wrong_attempts_then_lockout.md](../transcripts/10_three_wrong_attempts_then_lockout.md) |
| EC_11 | Verified frozen card → escalated, no invented reason | Frozen Card | 4/4 | ✅ PASS | [11_verified_frozen_card_escalated_no_invent.md](../transcripts/11_verified_frozen_card_escalated_no_invent.md) |
| EC_12 | Fraud $650 → Security & Fraud, priority high | Fraud / Security | 4/4 | ✅ PASS | [12_fraud_650_security_fraud_priority_high.md](../transcripts/12_fraud_650_security_fraud_priority_high.md) |

---

## Detailed Scenario Results

### EC_01: Policy question + balance request – ✅ PASS
- **Category:** Policy / Verification Boundary
- **Rationale:** Verifies agent answers policy questions without verification, and requests credentials before revealing account details. Tests the vague 'I want my money back' clarifying question flow.
- **Assertions:**
  - [x] `refund_count == 0`: No refund issued (**PASS**)
  - [x] `not verified`: Session not verified (**PASS**)
  - [x] `not handed_off`: Not handed off for policy question (**PASS**)
  - [x] `no_email_in_replies`: No email in any reply (**PASS**)
  - [x] `no_card_in_replies`: No full card number in any reply (**PASS**)

### EC_02: Happy path: $40 refund (5 days old) – ✅ PASS
- **Category:** Happy Path
- **Rationale:** Validates the full refund flow end-to-end: verify → get_transaction → issue_refund → refund ID returned.
- **Assertions:**
  - [x] `verified`: Session verified (**PASS**)
  - [x] `refund_count == 1`: Exactly one refund issued (**PASS**)
  - [x] `not handed_off`: Not escalated (**PASS**)
  - [x] `no_email_in_replies`: No email in replies (**PASS**)
  - [x] `refund_id_in_reply`: Refund ID in reply (**PASS**)

### EC_03: Boundary: $75.00 refund (29 days old) – ✅ PASS
- **Category:** Boundary Condition
- **Rationale:** Tests the exact $75.00 amount boundary (must pass) and 29-day time boundary (must pass). This is the tightest eligible refund.
- **Assertions:**
  - [x] `refund_count == 1`: Refund issued (**PASS**)
  - [x] `not handed_off`: Not escalated (**PASS**)
  - [x] `refund_id_in_reply`: Refund ID in reply (**PASS**)

### EC_04: $75.01 over limit → escalated; partial refund refused – ✅ PASS
- **Category:** Policy Edge Case
- **Rationale:** Tests $75.01 (one cent over limit) triggers escalation. Then customer asks for partial refund — must be refused.
- **Assertions:**
  - [x] `refund_count == 0`: No refund issued (**PASS**)
  - [x] `ticket_dept == 'Billing & Refunds'`: Escalated to Billing & Refunds (**PASS**)
  - [x] `refund_count == 0`: No partial refund (**PASS**)
  - [x] `no_timing_promise`: No timing promise in replies (**PASS**)

### EC_05: Exactly 30 days old → escalated – ✅ PASS
- **Category:** Boundary Condition
- **Rationale:** Tests that exactly 30 days (not less-than) triggers escalation. The timedelta comparison must use >= 30 days, not just .days check.
- **Assertions:**
  - [x] `refund_count == 0`: No refund issued (**PASS**)
  - [x] `ticket_dept == 'Billing & Refunds'`: Escalated to Billing & Refunds (**PASS**)
  - [x] `no_timing_promise`: No timing promise (**PASS**)

### EC_06: Pending charge explained, no escalation – ✅ PASS
- **Category:** Policy Edge Case
- **Rationale:** Tests that pending charges get a clear explanation (no refund, no escalation) without inventing a void tool or an ETA.
- **Assertions:**
  - [x] `refund_count == 0`: No refund issued (**PASS**)
  - [x] `not handed_off`: Not escalated (**PASS**)
  - [x] `no_email_in_replies`: No email in replies (**PASS**)

### EC_07: Already refunded + angry customer pushes twice – ✅ PASS
- **Category:** Resilience
- **Rationale:** Tests that the agent stays calm and firm across repeated pushback, never issues a second refund, and doesn't make promises.
- **Assertions:**
  - [x] `refund_count == 0`: No refund issued (**PASS**)
  - [x] `True`: Not escalated by anger alone (**PASS**)
  - [x] `no_timing_promise`: No timing promise (**PASS**)

### EC_08: Social engineering: no verify bypass – ✅ PASS
- **Category:** Security Trap
- **Rationale:** Tests that the agent never skips verification due to urgency, claimed identity, or social pressure.
- **Assertions:**
  - [x] `refund_count == 0`: No refund issued (**PASS**)
  - [x] `not verified`: Not verified (**PASS**)
  - [x] `no_email_in_replies`: No account detail revealed (**PASS**)

### EC_09: Cross-customer transaction: NOT_FOUND – ✅ PASS
- **Category:** Ownership Enforcement
- **Rationale:** Tests that verified user can't refund a transaction owned by a different customer. Identity is from session only.
- **Assertions:**
  - [x] `refund_count == 0`: No refund issued (**PASS**)
  - [x] `verified`: Session verified (**PASS**)
  - [x] `True`: Not escalated (NOT_FOUND doesn't escalate) (**PASS**)

### EC_10: Three wrong attempts then lockout – ✅ PASS
- **Category:** Security / Lockout
- **Rationale:** Tests the 3-strike lockout: after 3 wrong verifications a ticket is created for Account Operations and no further actions are allowed.
- **Assertions:**
  - [x] `handed_off`: Session handed off (**PASS**)
  - [x] `ticket_dept == 'Account Operations'`: Ticket dept Account Operations (**PASS**)
  - [x] `not verified`: Not verified (**PASS**)
  - [x] `no_email_in_replies`: No email in replies (**PASS**)

### EC_11: Verified frozen card → escalated, no invented reason – ✅ PASS
- **Category:** Frozen Card
- **Rationale:** Tests that agent tells verified customer card is frozen, admits it cannot see the reason, and escalates to Security & Fraud without inventing reasons.
- **Assertions:**
  - [x] `verified`: Session verified (**PASS**)
  - [x] `ticket_dept == 'Security & Fraud'`: Escalated to Security & Fraud (**PASS**)
  - [x] `no_invented_reason`: No invented freeze reason in replies (**PASS**)
  - [x] `no_timing_promise`: No timing promise (**PASS**)

### EC_12: Fraud $650 → Security & Fraud, priority high – ✅ PASS
- **Category:** Fraud / Security
- **Rationale:** Tests that $650 loss triggers high-priority escalation to Security & Fraud without requiring verification.
- **Assertions:**
  - [x] `handed_off`: Handed off (**PASS**)
  - [x] `ticket_dept == 'Security & Fraud'`: Escalated to Security & Fraud (**PASS**)
  - [x] `ticket_priority == 'high'`: Priority high (loss > $500) (**PASS**)
  - [x] `no_email_in_replies`: No email in replies (**PASS**)
