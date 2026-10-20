# Architectural & Policy Questions for Fini

During the hardening and refactoring of the Paylane Support Agent, our engineering team identified several architectural trade-offs and domain boundary decisions that warrant discussion. Here are four key questions for the Fini product and engineering team:

---

### 1. Priority Field in `escalate_to_human`: LLM Discretion vs. Deterministic Code
In the original take-home brief, `escalate_to_human` allowed the model to specify both `priority` (`high` vs `normal`) and arbitrary department names. In our refactored architecture, we removed `priority` from model arguments and calculated it strictly in code (`reported_loss_usd > 500`, 3-strike lockouts, and frozen cards receive `high`, all others `normal`), while validating `department` against an immutable enum.
- **Question:** Is it Fini's standard production pattern to compute escalation priority and severity strictly via deterministic business rules and heuristic intent parsing, or does Fini prefer a hybrid approach where the LLM proposes priority subject to programmatic guardrails and policy ceilings?

---

### 2. Transaction Discovery vs. Mandatory Transaction IDs
Currently, the policy requires the customer to provide a specific `txn_id` before querying status or requesting a refund. The agent is explicitly forbidden from searching transactions or guessing which charge the customer means. However, in real-world support, customers frequently say *"I want a refund for that $40 coffee charge from Tuesday"* without knowing their internal transaction identifier.
- **Question:** Would Fini consider introducing a scoped, read-only `list_recent_transactions(limit=5)` tool for verified sessions, or should support agents always enforce a strict requirement for explicit transaction IDs to minimize hallucination and misattribution risks?

---

### 3. Handling Frozen Cards and Root-Cause Transparency
The policy strictly mandates that the agent must state the card is frozen, state that it cannot see why and cannot unfreeze it, and escalate to `Security & Fraud` without guessing. In production, this causes immediate customer anxiety when an AI agent announces a freeze without context.
- **Question:** What is Fini’s recommended design pattern for sensitive account status flags? Should the core banking service expose customer-friendly sanitized status codes (e.g., `SUSPECTED_PHISHING`, `EXPIRED_KYC`, `TRAVEL_BLOCK`) that the agent is authorized to share, or is immediate human escalation with zero detail the preferred defense-in-depth posture?

---

### 4. Verification Architecture: Session Store State vs. Distributed Token/JWT State
We eliminated identity parameters (`customer_id`) from tool signatures and bound customer identity strictly to server-side `SessionState` held by the FastAPI service. The LangGraph agent only holds conversation message history, passing `session_id` via runtime config.
- **Question:** In high-scale distributed deployments across multiple agent pods, does Fini typically back session verification state with a centralized Redis/database store, or use cryptographic session bearer tokens (e.g., short-lived signed JWTs issued upon verification) passed in HTTP headers?
