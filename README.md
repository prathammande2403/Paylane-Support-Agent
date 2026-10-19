# Paylane AI Support Agent

A production-grade, code-based customer support AI agent built for **Paylane** (fintech app handling cards, transfers, and refunds). Powered by **LangGraph**, deterministic Python tool guardrails, and pre-flight intake filtering.

---

## 🚀 Architecture Highlights

1. **Pre-Flight Intake Filter (`agent/intake_filter.py`)**: Filters out non-existent users, unauthorized accounts, and expired support windows before wasting LLM API tokens.
2. **LangGraph State Management (`agent/graph.py`)**: Tracks customer verification flags, failed retry counters, unresolved turn stagnation, and escalation states.
3. **Deterministic Tool Guardrails (`agent/tools.py`)**: Enforces strict business policies in Python code (max $75 auto-approval, < 30 days old, settled status only, no duplicate refunds, frozen card restrictions).
4. **Departmental Routing & Priority Matrix**: Automatically categorizes escalated tickets into `Billing & Refunds`, `Security & Fraud`, `Legal & Compliance`, `Account Operations`, or `General Support`.

---

## 🛠️ Setup Instructions

### 1. Clone & Install Dependencies
```bash
git clone <repository-url>
cd paylane-support-agent
pip install -r requirements.txt