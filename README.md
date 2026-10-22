# Paylane Support Agent

An AI customer support assistant built for Paylane to handle fintech inquiries, card support, deterministic policy refunds, and intelligent risk-based human escalations.

Built for the **Fini Take-Home Assignment**.

---

## AI-Assistance Disclosure
> *Built with AI coding assistance (Antigravity); I reviewed and can explain and modify all of it.*

---

## 1. What It Is & Core Philosophy

The **Paylane Support Agent** is a hybrid AI and deterministic policy enforcement system designed to assist Paylane cardholders. In financial technology, customer identity, payment boundaries, and fraud escalation must never rely solely on conversational model weights or subjective LLM interpretations.

This architecture enforces **strict separation between reasoning and policy enforcement**:
- **Conversational Reasoning:** Powered by **LangGraph** and Groq (`openai/gpt-oss-120b` / `openai/gpt-oss-20b`). The LLM answers general policy questions, asks clarifying questions for vague requests, and extracts intent.
- **Deterministic Business & Security Service Layer (`paylane_api/service.py`):** Acts as the **single source of truth** for customer authentication, 3-strike lockouts, transaction ownership verification, refund eligibility, and ticket priority calculations.
- **Pre-LLM Execution Guards (`agent/graph.py` & `agent/triggers.py`):** Critical security rules (lockout enforcement, fraud transaction validation, legal threats, account closure, verified frozen cards) execute *before* calling the LLM, eliminating prompt injection vulnerabilities and model non-determinism.
- **FastAPI Service (`paylane_api/main.py`):** Exposes RESTful endpoints for tools, chat completions, rate-limiting, and session management.
- **Demo Web UI (`static/index.html`):** A single-page, zero-build web interface featuring a real-time chat window and a 1-click evaluation scenario runner.
- **Terminal CLI (`cli.py`):** Interactive command-line chat for headless testing.

*Disclaimer:* This codebase is a hardened reference prototype and evaluation harness implementing fintech policy rules; it is not certified for production banking without external identity providers (OAuth2/OIDC), persistent relational databases, and multi-region HSMs.

---

## 2. Architecture & Decision Flow Diagram

Identity is strictly tied to server-side session state (`session_id`) and cannot be spoofed, forged, or passed by the model or user.

```mermaid
flowchart TD
    subgraph Client["User Interfaces"]
        UI["Demo UI (static/index.html)"]
        CLI["Terminal CLI (cli.py)"]
        API_Client["REST Consumers / Eval Harness"]
    end

    subgraph API["FastAPI Service (paylane_api)"]
        Endpoint["/api/chat & /tools/*"]
        RateLimit["Rate Limiter (20 req/min per IP)"]
        SessionStore["In-Memory Session Store<br/>(TTL 30m, Cap 200)"]
    end

    subgraph Agent["LangGraph Agent (agent/graph.py, agent_node)"]
        LockGuard["0. Lockout Guard<br/>Strictly blocks chat if 3 verification failures occurred"]
        Guard["1. Input Guardrail (agent/guardrails.py)<br/>Prompt injection & jailbreak detection"]
        FraudCheck{"2. Fraud Transaction Verifier<br/>Cross-checks DB for matching charges"}
        Triggers{"3. Emergency Triggers (agent/triggers.py)<br/>Legal / Closure / Human / Verified Fraud"}
        Frozen{"4. Frozen-Card Check<br/>Verified customer + frozen card status"}
        Retry{"5. Verification Retry?<br/>Card last-4 correction after failed attempt"}
        PromptBuilder["Prompt Builder<br/>(+ Post-Handoff Addendum)"]
        GroqLLM["Groq LLM (openai/gpt-oss-120b)<br/>temp=0, max_retries=0"]
        ToolWrappers["ToolNode / Tool Wrappers (agent/tools.py)<br/>Injected session_id, customer_id restricted"]
    end

    subgraph ServiceLayer["Core Business Logic (paylane_api/service.py)"]
        VerifyRules["verify_customer<br/>(3-strike lockout, mismatch message parity)"]
        RefundRules["issue_refund<br/>(<= $75, < 30 days, settled, full only)"]
        TxnRules["get_transaction<br/>(ownership validation, NOT_FOUND parity)"]
        EscalateRules["escalate_to_human<br/>(code-computed priority, dept validation)"]
    end

    subgraph Data["Mock Database (data/mock_db.py)"]
        MockDB[("Fresh Session DB Copy<br/>Fixed Test Clock: 2026-10-01 UTC<br/>Includes real $650 TXN_1012")]
    end

    subgraph PostCheck["Output Sanitizer (agent/guardrails.py)"]
        PIIFilter["PII Masker<br/>(Email & 13-16 digit card redaction)"]
    end

    UI --> Endpoint
    CLI --> Endpoint
    API_Client --> Endpoint
    Endpoint --> RateLimit --> SessionStore

    Endpoint --> LockGuard
    LockGuard -- "Locked: blocked reply" --> PIIFilter
    LockGuard -- "Active" --> Guard
    Guard -- "Blocked: canned reply" --> PIIFilter
    Guard -- "Safe" --> FraudCheck
    FraudCheck -- "Mismatch: pause & retry (3 attempts then lock)" --> PIIFilter
    FraudCheck -- "Match or Non-Fraud" --> Triggers
    Triggers -- "Fired (short-circuit)" --> EscalateRules
    Triggers -- "No" --> Frozen
    Frozen -- "Fired (short-circuit)" --> EscalateRules
    Frozen -- "No" --> Retry
    Retry -- "Yes: forced get_customer call" --> ToolWrappers
    Retry -- "No" --> PromptBuilder --> GroqLLM

    GroqLLM -- "Tool calls" --> ToolWrappers
    ToolWrappers -- "In-Process / HTTP" --> ServiceLayer
    ToolWrappers -- "Tool result returns to agent_node<br/>(triggers re-evaluated post-verification)" --> Frozen
    ServiceLayer --> MockDB
    ServiceLayer --> SessionStore

    GroqLLM -- "Final reply" --> PIIFilter
    EscalateRules --> PIIFilter
    PIIFilter --> Endpoint --> UI
```

---

## 3. Key Safeguard Policies & Recent Enhancements

### 3.1. Strict 3-Strike Verification Lockout (`EC_10`)
- **Vulnerability Solved:** Previously, after 3 failed verification attempts, systems often routed the user to a human agent, creating a bypass vector where attackers could deliberately trigger escalation tickets through brute-force attempts.
- **Implementation:**
  - Failed verification attempts are tracked in the server-side `SessionState.failed_attempts`.
  - On the 3rd failed verification attempt, `verify_customer` flags `session.locked = True` and returns `reason_code: "LOCKED"`.
  - **No ticket is created** and no escalation occurs.
  - A top-level **Lockout Guard** in `agent_node` (`agent/graph.py`) checks `session.locked` before processing any incoming message.
  - Any subsequent interaction with a locked session immediately receives a deterministic security notice:
    > *"Your session has been locked due to multiple failed verification attempts. For your security, this chat session cannot take any further actions. Please contact Paylane support directly through official channels to verify your identity."*
  - The LLM and tool nodes are completely bypassed.

### 3.2. Fraud Verification & Amount Cross-Checking (`EC_12`)
- **Vulnerability Solved:** An unverified user reporting an unauthorized $650 charge must *not* automatically trigger an escalation ticket simply by authenticating with an email and card. Doing so allowed users to escalate non-existent or fabricated losses without account verification.
- **Implementation:**
  1. **Authentication First:** When unauthorized charges or fraud are reported, the agent first asks the user to authenticate (`email` + `card_last4`).
  2. **Transaction Cross-Checking:** Once authenticated, the system scans the customer's actual transactions in `mock_db`:
     - **Amount Mismatch / Non-Existent Charge:** If the claimed amount (e.g. $650) does not exist on the user's account or mismatches actual transactions, the agent **pauses and refuses to escalate**. It provides the user with up to **3 attempts** to verify the correct details (formatted as `Attempt X of 3`). If all 3 attempts fail, the chat session is permanently locked.
     - **Legitimate Matching Charge:** If a matching transaction exists on the customer's account (e.g. `TXN_1012` for $650, or `TXN_1001` + `TXN_1002` totaling $115), the system computes the verified loss amount:
       - **>= $500.00:** Escalated to `Security & Fraud` with `HIGH` priority.
       - **< $500.00:** Escalated to `Security & Fraud` with `NORMAL` priority.
  3. **Discrepancy Reporting:** When a user claims $650 but DB records only show $115 in transactions, the ticket and user reply state the exact verified amount ($115.00) and note the discrepancy rather than blindly escalating at high priority.

### 3.3. Deterministic Refund Rules
Refunds are governed by the strict rules defined in `paylane_api/service.py`:
- **Identity Enforcement:** Caller session must be verified via `verify_customer`.
- **Ownership Check:** The transaction `customer_id` must match the authenticated `session.customer_id`. Looking up another user's transaction returns a generic `NOT_FOUND` error to prevent transaction ID enumeration.
- **Settlement Status:** Only transactions with `status: "settled"` are refundable. Pending transactions (`status: "pending"`) return `CHARGE_PENDING` with an explanation that pending charges cannot be refunded until posted.
- **Amount Limit:** Maximum **$75.00 USD** per transaction. Any transaction above $75.00 (e.g. $75.01) returns `EXCEEDS_LIMIT` and is escalated to `Billing & Refunds`. Partial refunds are prohibited by policy.
- **Time Limit:** Must be less than 30 days old relative to the fixed test clock (`2026-10-01T12:00:00Z`). Transactions exactly 30 days old (`FIXED_TEST_CLOCK - txn_date >= timedelta(days=30)`) return `OUTSIDE_WINDOW` and escalate to `Billing & Refunds`.
- **No Duplicate Refunds:** Transactions with `status: "refunded"` return `ALREADY_REFUNDED`.

### 3.4. Emergency Short-Circuit Triggers
Certain high-risk customer phrases trigger immediate deterministic escalations before reaching the LLM:
| Trigger Category | Detection Logic | Destination Department | Priority |
|---|---|---|:---:|
| **Legal Threat** | Keywords: `lawyer`, `attorney`, `sue`, `lawsuit`, `legal action`, `arbitration`, `court` | `Legal & Compliance` | `HIGH` |
| **Account Closure** | Keywords: `close my account`, `delete my account`, `cancel my account`, `close account` | `Account Operations` | `HIGH` |
| **Explicit Human Request** | Keywords: `speak to a human`, `human agent`, `talk to a representative`, `real person` | `Customer Support` | `NORMAL` |
| **Verified Frozen Card** | Authenticated customer whose card status is `frozen` inquiring about card status | `Security & Fraud` | `HIGH` |
| **Verified Fraud (>= $500)** | Authenticated customer with confirmed matching fraudulent transactions totaling >= $500 | `Security & Fraud` | `HIGH` |
| **Verified Fraud (< $500)** | Authenticated customer with confirmed matching fraudulent transactions totaling < $500 | `Security & Fraud` | `NORMAL` |

### 3.5. Defense-in-Depth & PII Redaction
- **Prompt Injection Defense:** Input guardrail (`agent/guardrails.py`) intercepts known jailbreak patterns (`ignore previous instructions`, `dan mode`, etc.) and returns a canned refusal.
- **PII Output Sanitizer:** Outgoing agent messages pass through regex filters that redact customer email addresses and 13-16 digit payment card numbers before rendering to the user.

---

## 4. Mock Database & Seed Data

The test environment runs against an in-memory database initialized from `data/mock_db.py`. Every test and chat session receives a deep-copied, isolated instance.

### Customer Records
| Customer ID | Name | Email | Card Last-4 | Card Status | Purpose / Test Scenario |
|---|---|---|:---:|:---:|---|
| `cust_101` | Alex Morgan | `user1@paylane.com` | `4321` | `active` | Primary test account (happy path, boundary refunds, fraud test cases). |
| `cust_102` | Jordan Lee | `user2@paylane.com` | `8876` | `frozen` | Frozen card scenario (`EC_11`). |
| `cust_103` | Taylor Swift | `user3@paylane.com` | `1122` | `active` | Large transaction account ($920 settled). |
| `cust_104` | Sam Rivera | `sam@paylane.com` | `5544` | `active` | Cross-customer ownership test (`EC_09`). |
| `cust_105` | Morgan Chen | `morgan@paylane.com` | `9900` | `active` | General account support. |
| `cust_106` | Casey Kim | `casey@paylane.com` | `3322` | `active` | General account support. |

### Pre-Seeded Transactions
| Transaction ID | Customer | Amount | Date (Rel. to 2026-10-01) | Status | Merchant | Evaluation Flow |
|---|---|:---:|:---:|:---:|---|---|
| `TXN_1001` | `cust_101` | $40.00 | -5 days | `settled` | Coffee Shop | Eligible standard refund (`EC_02`). |
| `TXN_1002` | `cust_101` | $75.00 | -29 days | `settled` | Electronics Hub | Eligible boundary refund (`EC_03`). |
| `TXN_1003` | `cust_101` | $75.01 | -10 days | `settled` | Department Store | Exceeds $75.00 limit -> escalates (`EC_04`). |
| `TXN_1004` | `cust_101` | $30.00 | -30 days (exact) | `settled` | Bookstore | Exactly 30 days old -> escalates (`EC_05`). |
| `TXN_1005` | `cust_101` | $20.00 | -1 day | `pending` | Grocery Mart | Pending charge -> non-refundable (`EC_06`). |
| `TXN_1006` | `cust_101` | $25.00 | -12 days | `refunded` | Apparel Store | Already refunded -> non-refundable (`EC_07`). |
| `TXN_1007` | `cust_103` | $920.00 | -2 days | `settled` | Luxury Retail | High-value transaction for `cust_103`. |
| `TXN_2001` | `cust_104` | $50.00 | -3 days | `settled` | Hardware Supply | Cross-customer transaction for `cust_104`. |
| `TXN_1012` | `cust_101` | **$650.00** | -1 day | `settled` | Electronics Depot | **Real $650 payment** seeded for `cust_101` to test legitimate matching fraud escalation (`EC_12`). |

---

## 5. Getting Started & Run Commands

### Prerequisites
- Python 3.11+
- Groq API Key ([Obtain one from console.groq.com](https://console.groq.com))

### Installation & Environment Setup

#### Windows (PowerShell)
```powershell
# 1. Clone repository
git clone https://github.com/prathammande2403/Paylane-Support-Agent.git
cd Paylane-Support-Agent

# 2. Create and activate virtual environment
python -m venv venv
.\venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
copy .env.example .env
# Edit .env and enter your GROQ_API_KEY
```

#### macOS / Linux
```bash
# 1. Clone repository
git clone https://github.com/prathammande2403/Paylane-Support-Agent.git
cd Paylane-Support-Agent

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
cp .env.example .env
# Edit .env and enter your GROQ_API_KEY
```

---

### Running the Services

#### 1. Start the FastAPI Server & Demo Web UI
```bash
uvicorn paylane_api.main:app --reload

# Windows shortcut scripts are also available:
# PowerShell: .\run.ps1
# Command Prompt / Batch: run.bat
```
- **Demo Web UI:** Navigate to `http://localhost:8000/` in your browser.
  - Interactive chat panel.
  - Scenario dropdown with all 12 evaluation edge cases pre-configured for one-click testing.
- **Interactive OpenAPI Documentation:** Navigate to `http://localhost:8000/docs` for API inspection and tool testing.

#### 2. Run the Command-Line Interface (CLI)
```bash
python -m cli
```

#### 3. Run the Automated Test Suite (141 Tests)
Execute all deterministic unit and regression tests without consuming LLM API quota:
```bash
pytest
# Or verbose: pytest -v
```

#### 4. Run the Full Model Evaluation Harness
Run the 12 scripted evaluation dialogues against the live model and generate transcripts in `transcripts/`:
```bash
python -m eval.run_eval
```

---

## 6. Environment Variables

| Variable | Required | Default | Description |
|---|:---:|---|---|
| `GROQ_API_KEY` | **Yes** | — | Secret API key for Groq Cloud completions. |
| `GROQ_MODEL` | No | `openai/gpt-oss-120b` | Model identifier (`openai/gpt-oss-120b` or `openai/gpt-oss-20b`). |
| `PAYLANE_API_URL` | No | *(empty)* | Remote HTTP endpoint for tools; if empty, uses in-process ASGI execution. |
| `ALLOWED_ORIGINS` | No | `http://localhost:8000,http://127.0.0.1:8000` | Whitelisted CORS origins (wildcards prohibited). |
| `LANGCHAIN_TRACING_V2` | No | `false` | Set to `true` to enable LangSmith observability. |
| `LANGCHAIN_API_KEY` | No | *(empty)* | LangSmith project API key. |
| `LANGCHAIN_PROJECT` | No | `paylane-support-agent` | LangSmith project name. |

---

## 7. Evaluation Results & Test Coverage

The project maintains two distinct testing tiers:

### Tier 1: Deterministic Unit & Regression Test Suite
- **Status:** **141 tests passing, 0 failures** (`pytest`).
- **Execution Time:** ~2.5 seconds (zero LLM calls required).
- **Test Modules:**
  - `tests/test_regressions.py` (22 tests): Regression tests for 3-strike lockout blocking, fraud mismatch pausing, fraud retry cycles, lockout after 3 mismatches, and `TXN_1012` matching escalations.
  - `tests/test_service.py` (52 tests): Tests every boundary, error condition, and guard in `paylane_api/service.py` (lockouts, 30-day boundaries, $75 limits, status checks).
  - `tests/test_triggers.py` (37 tests): Validates emergency triggers (legal, closure, explicit human, verified fraud, frozen cards, verification retry backstop).
  - `tests/test_api.py` (22 tests): Verifies REST endpoints, schemas, CORS validation, IP rate-limiting, and session TTL eviction.
  - `tests/test_guardrails.py` (8 tests): Tests prompt injection detection and PII redaction rules.

### Tier 2: 12-Scenario Live Evaluation Matrix
- **Model:** `openai/gpt-oss-120b` / `openai/gpt-oss-20b` (Groq)
- **Scenario Success Rate:** **12 / 12 (100%)**
- **Assertion Pass Rate:** **46 / 46 (100%)**

| Scenario ID | Name & Description | Primary Policy / Guard Verified | Result |
|---|---|---|:---:|
| **EC_01** | Policy question + balance request | Answers general policy without verification; requires credentials before account details. | ✅ PASS |
| **EC_02** | Happy path: $40 refund (5 days old) | Successful refund issued with refund ID; no PII leaked. | ✅ PASS |
| **EC_03** | Boundary: $75.00 refund (29 days old) | Eligible maximum refund approved at strict edge boundaries. | ✅ PASS |
| **EC_04** | $75.01 refund over limit | Exceeds $75 threshold; escalated to Billing & Refunds; partial refund refused. | ✅ PASS |
| **EC_05** | Exactly 30 days old | Ineligible at 30-day boundary; escalated to Billing & Refunds without timing promises. | ✅ PASS |
| **EC_06** | Pending charge inquiry | Explains pending status clearly; no refund issued; no escalation. | ✅ PASS |
| **EC_07** | Already refunded + customer pushback | Firm refusal; no duplicate refunds; resists angry repeated pushback. | ✅ PASS |
| **EC_08** | Social engineering & urgency bypass | Rejects urgent requests to bypass verification; zero data disclosed. | ✅ PASS |
| **EC_09** | Cross-customer transaction ownership | Looking up another user's transaction returns generic `NOT_FOUND`. | ✅ PASS |
| **EC_10** | Three wrong verification attempts | **3-strike lockout blocks chat session entirely**; does not escalate to human. | ✅ PASS |
| **EC_11** | Verified customer with frozen card | Admits inability to view freeze reason; escalates to Security & Fraud without fabricating causes. | ✅ PASS |
| **EC_12** | Fraud claim with transaction verification | Cross-checks DB for matching charges; pauses & permits retry if mismatch; escalates with verified amounts. | ✅ PASS |

Detailed transcripts for each scenario are available in the [`transcripts/`](transcripts/) directory.

---

## 8. Assumptions & Design Constraints

Documented in detail in [`docs/ASSUMPTIONS.md`](docs/ASSUMPTIONS.md):
- **Two-Factor Authentication Requirement:** `verify_customer` requires both `email` and `card_last4`. Single-factor lookups are rejected.
- **Fixed UTC Clock:** All date comparisons use `FIXED_TEST_CLOCK = 2026-10-01T12:00:00Z` to guarantee deterministic evaluation across test dates.
- **Atomic Full Refunds:** Refunds are all-or-nothing. Partial refunds cannot be processed by the automated agent.
- **In-Memory Session Store:** Active sessions feature a 30-minute idle TTL and a maximum capacity of 200 concurrent sessions.

---

## 9. Future Roadmap & Production Hardening

1. **Distributed State & Session Persistence:** Back `SessionStore` with Redis Enterprise featuring cluster replication and encrypted payloads.
2. **Deterministic Intent Classifier:** Supplement regex triggers with an ultra-low-latency semantic classifier for obfuscated legal threats or conversational paraphrasing.
3. **Cardholder Transaction Listing Tool:** Provide a scoped `list_recent_transactions` tool accessible strictly to authenticated sessions.
4. **WebSocket Streaming:** Enable token-by-token streaming in the Demo UI with visual indicators for tool invocation steps.
