# Engineering Assumptions & Conservative Policy Interpretations

This document records all architectural decisions, ambiguous requirements from the project brief, and the conservative interpretations implemented in the **Paylane Support Agent**.

---

## 1. Tool Signatures & Customer Identity
- **Ambiguity in Brief:** The project brief mentions `get_customer(email)` in Section 3 mock record shape, but Section 2 states: *"Before discussing ANY account detail the customer must supply both the email and the last 4 card digits and both must match the mock record."*
- **Conservative Decision:** `get_customer` requires both `email` and `card_last4`. Verification cannot succeed with email alone. Furthermore, `customer_id` was completely removed from all model-visible tool signatures (`issue_refund`, `get_transaction`, `get_customer`). The agent's identity context is derived solely from the server-side `SessionState` associated with `config["configurable"]["thread_id"]`.
- **Reason:** Prevents any prompt injection or LLM hallucination from overriding session identity or accessing other customer accounts.

---

## 2. Priority Calculation for Escalations
- **Ambiguity in Brief:** Section 1 Bug 9 states: *"escalate_to_human lets the model choose priority and department freely. Priority must be computed in code and department validated against an allowed set."*
- **Conservative Decision:** In `paylane_api/service.py`, `priority` is computed purely in code:
  - `priority = "high"` if `reported_loss_usd is not None and reported_loss_usd > 500.0`.
  - `priority = "high"` for 3-strike account lockouts (Account Operations) and frozen cards (Security & Fraud).
  - `priority = "normal"` for all other escalations (including fraud reports under $500 or with unstated loss amount).
- **Reason:** The LLM cannot inflate ticket priorities based on user urgency or anger.

---

## 3. Strict 30-Day Boundary Rule
- **Ambiguity in Brief:** *"elapsed time since transaction < 30 days using UTC timestamps and the fixed test clock (exactly 30 days escalates; compare with timedelta(days=30), not just .days)"*
- **Conservative Decision:** The check is implemented as:
  ```python
  age = FIXED_TEST_CLOCK - txn_date
  if age >= timedelta(days=30):
      # TOO_OLD -> Escalate to Billing & Refunds
  ```
  Transactions that are precisely 30 days, 0 seconds old are considered 30 days old and are therefore escalated to human review rather than auto-approved.

---

## 4. Refund Scope: Full Amounts Only
- **Ambiguity in Brief:** Could a customer request a split or partial refund?
- **Conservative Decision:** Partial refunds are strictly disallowed (`PARTIAL_NOT_ALLOWED`). If a customer requests a partial refund or the tool receives `amount != txn["amount"]`, it returns `{ok: False, reason: "Only full original transaction amount ($...) can be refunded.", reason_code: "PARTIAL_NOT_ALLOWED"}`. Split requests or partial auto-refunds are blocked.

---

## 5. Frozen Cards & Root Cause Visibility
- **Ambiguity in Brief:** How should the agent address customers asking why their card is frozen?
- **Conservative Decision:** The agent is explicitly prohibited from guessing or fabricating reasons for a card freeze (e.g., suspicious activity, security breach, fraud). The agent states that the card is frozen, acknowledges that it cannot see the freeze reason and cannot unfreeze the card, and escalates the ticket to `Security & Fraud`.

---

## 6. Pending Charges Policy
- **Ambiguity in Brief:** How to treat pending transactions?
- **Conservative Decision:** There is no "void" tool and no estimated settlement arrival date. The agent informs the customer that pending transactions cannot be refunded until they post/settle, and does not escalate to a human unless the customer explicitly requests human support.

---

## 7. Guest Tickets for Unauthenticated Handoffs
- **Ambiguity in Brief:** How to log tickets when a customer requests a human, legal escalation, or account closure without verifying first?
- **Conservative Decision:** The session logs the ticket with `customer_id = "GUEST"` (or `None`). The user is never blocked from emergency assistance (legal, regulatory, account closure, explicit human request) by a verification barrier.

---

## 8. Output PII Redaction
- **Ambiguity in Brief:** How strictly should output PII be masked?
- **Conservative Decision:** All email addresses (`[\w\.-]+@[\w\.-]+\.\w+`) and all 13–16 digit credit card sequences are masked to `[redacted]` before any assistant text is returned to the user or stored in tickets. Bare 4-digit card references (e.g., "ending in 4321") remain intact for conversational clarity.

---

## 9. Single Source of Truth for Session State
- **Ambiguity in Brief:** Storing state across LangGraph and FastAPI.
- **Conservative Decision:** All mutable state (`db`, `verified`, `customer_id`, `failed_attempts`, `handed_off`, `ticket`, `trace`) lives in `paylane_api.sessions.session_store`. LangGraph `PaylaneState` only tracks conversation message history (`messages`). Before invoking the model or executing tools, the agent consults `session_store` directly.
