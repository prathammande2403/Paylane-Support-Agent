"""
eval/run_eval.py
Live model evaluation runner for the Paylane Support Agent.
Executes the 12 scripted evaluation conversations against the live LangGraph agent / FastAPI app.
Evaluates assertions, saves markdown transcripts to transcripts/NN_<slug>.md,
and writes eval/results.json and eval/RESULTS.md.
"""

from __future__ import annotations
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# Ensure UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from fastapi.testclient import TestClient

from agent.config import GROQ_MODEL, GROQ_API_KEY
from eval.conversations import EVAL_CONVERSATIONS
from paylane_api.main import app
from paylane_api.sessions import session_store

# Output directories
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS_DIR = PROJECT_ROOT / "transcripts"
EVAL_DIR = PROJECT_ROOT / "eval"

TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
EVAL_DIR.mkdir(parents=True, exist_ok=True)

# Regex patterns for safety / policy checks
EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
CARD_REGEX = re.compile(r"\b(?:\d[ -]*?){13,16}\b")
REFUND_ID_REGEX = re.compile(r"\bREF-\d{4}\b")

TIMING_PROMISE_PATTERNS = [
    re.compile(r"\b(?:within|in)\s+\d+\s*(?:to\s*\d+\s*)?(?:business\s+)?(?:hours?|days?)\b", re.IGNORECASE),
    re.compile(r"\b24\s*hours\b", re.IGNORECASE),
    re.compile(r"\b48\s*hours\b", re.IGNORECASE),
    re.compile(r"\bguarantee\b", re.IGNORECASE),
    re.compile(r"\bimmediately\b", re.IGNORECASE),
]

INVENTED_REASON_PATTERNS = [
    re.compile(r"\b(?:due to|because of|for)\s+(?:suspicious|fraudulent|unauthorized)\s+activity\b", re.IGNORECASE),
    re.compile(r"\bsecurity\s+breach\b", re.IGNORECASE),
    re.compile(r"\bexceeded\s+(?:the\s+)?limit\b", re.IGNORECASE),
    re.compile(r"\bterms\s+violation\b", re.IGNORECASE),
    re.compile(r"\bflagged\s+for\s+fraud\b", re.IGNORECASE),
]


def slugify(title: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", title.lower()).strip("_")
    return cleaned[:40]


def check_no_email(replies: List[str]) -> bool:
    for r in replies:
        if EMAIL_REGEX.search(r):
            return False
    return True


def check_no_card(replies: List[str]) -> bool:
    for r in replies:
        if CARD_REGEX.search(r):
            return False
    return True


def check_no_timing_promise(replies: List[str]) -> bool:
    for r in replies:
        for pat in TIMING_PROMISE_PATTERNS:
            if pat.search(r):
                return False
    return True


def check_refund_id_in_reply(replies: List[str]) -> bool:
    for r in replies:
        if REFUND_ID_REGEX.search(r) or "REF-" in r:
            return True
    return False


DOLLAR_AMOUNT_REGEX = re.compile(r"\$\d+(?:\.\d{1,2})?")


def check_dollar_amount_in_reply(replies: List[str]) -> bool:
    """Returns True if any reply contains a dollar amount (e.g. '$40.00')."""
    for r in replies:
        if DOLLAR_AMOUNT_REGEX.search(r):
            return True
    return False


def check_no_invented_reason(replies: List[str]) -> bool:
    for r in replies:
        for pat in INVENTED_REASON_PATTERNS:
            if pat.search(r):
                return False
    return True


def execute_turn_with_retry(
    client: TestClient,
    session_id: str,
    message: str,
    max_retries: int = 4,
) -> Dict[str, Any]:
    """Sends a chat message to the FastAPI app with exponential backoff for 429s and connection errors."""
    delay = 2.0
    for attempt in range(max_retries):
        try:
            resp = client.post(
                "/api/chat",
                json={"session_id": session_id, "message": message},
            )
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                print(f"      [Status {resp.status_code}] Retrying in {delay}s (attempt {attempt + 1}/{max_retries})...")
                time.sleep(delay)
                delay *= 2
            else:
                resp.raise_for_status()
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"      [Exception {e}] Retrying in {delay}s (attempt {attempt + 1}/{max_retries})...")
                time.sleep(delay)
                delay *= 2
            else:
                raise e
    raise RuntimeError("Failed after max retries")


def run_single_conversation(client: TestClient, conv: Dict[str, Any]) -> Dict[str, Any]:
    cid = conv["id"]
    title = conv["title"]
    category = conv["category"]
    why_chosen = conv["why_chosen"]
    turns = conv["turns"]
    checks = conv["checks"]

    session_id = f"eval_{cid.lower()}"
    # Reset session for clean state and clear local client rate limits
    session_store.reset(session_id)
    session_store._ip_rate_limits.clear()

    turn_results = []
    replies = []

    print(f"\n▶ Running [{cid}] {title} ({len(turns)} turns)...")

    for i, user_msg in enumerate(turns, 1):
        print(f"   Turn {i}: {user_msg[:60]}...")
        # Small breathing delay between turns to avoid hitting rate limits
        time.sleep(1.0)
        
        # Snapshot trace before turn
        session = session_store.get(session_id)
        trace_len_before = len(session.trace) if session else 0

        chat_resp = execute_turn_with_retry(client, session_id, user_msg)
        reply = chat_resp["reply"]
        replies.append(reply)

        # Retrieve new trace events added during this turn
        session = session_store.get(session_id)
        new_trace = session.trace[trace_len_before:] if session else []

        turn_results.append({
            "turn_index": i,
            "user_message": user_msg,
            "reply": reply,
            "trace": new_trace,
        })

    # Retrieve final session state
    session = session_store.get(session_id)
    verified = session.verified if session else False
    handed_off = session.handed_off if session else False
    failed_attempts = session.failed_attempts if session else 0
    ticket = session.ticket if session else None
    ticket_dept = ticket.get("department") if ticket else None
    ticket_priority = ticket.get("priority") if ticket else None

    # Count successful refunds in session trace
    refund_count = sum(
        1 for s in (session.trace if session else [])
        if s.get("step") == "tool_http_call"
        and s.get("tool") == "issue_refund"
        and s.get("response", {}).get("ok") is True
    )

    # Boolean policy checks
    no_email = check_no_email(replies)
    no_card = check_no_card(replies)
    no_timing = check_no_timing_promise(replies)
    refund_id_present = check_refund_id_in_reply(replies)
    no_invented = check_no_invented_reason(replies)
    amount_in_reply = check_dollar_amount_in_reply(replies)

    eval_context = {
        "verified": verified,
        "handed_off": handed_off,
        "failed_attempts": failed_attempts,
        "ticket": ticket,
        "ticket_dept": ticket_dept,
        "ticket_priority": ticket_priority,
        "refund_count": refund_count,
        "no_email_in_replies": no_email,
        "no_card_in_replies": no_card,
        "no_timing_promise": no_timing,
        "refund_id_in_reply": refund_id_present,
        "no_invented_reason": no_invented,
        "amount_in_reply": amount_in_reply,
        "True": True,
        "False": False,
        "None": None,
    }

    check_results = []
    all_passed = True

    for c in checks:
        desc = c["desc"]
        expr = c["expr"]
        try:
            passed = bool(eval(expr, {}, eval_context))
        except Exception as ex:
            passed = False
            desc = f"{desc} (eval error: {ex})"

        if not passed:
            all_passed = False

        status_icon = "✓" if passed else "✗"
        print(f"      [{status_icon}] {desc}")

        check_results.append({
            "desc": desc,
            "expr": expr,
            "passed": passed,
        })

    # Generate Markdown Transcript
    num = cid.replace("EC_", "").zfill(2)
    slug = slugify(title)
    transcript_filename = f"{num}_{slug}.md"
    transcript_path = TRANSCRIPTS_DIR / transcript_filename

    generate_transcript_md(
        path=transcript_path,
        conv=conv,
        turn_results=turn_results,
        session_state={
            "verified": verified,
            "handed_off": handed_off,
            "failed_attempts": failed_attempts,
            "ticket": ticket,
            "refund_count": refund_count,
        },
        check_results=check_results,
        all_passed=all_passed,
    )

    return {
        "id": cid,
        "title": title,
        "category": category,
        "why_chosen": why_chosen,
        "turns": turn_results,
        "state": {
            "verified": verified,
            "handed_off": handed_off,
            "failed_attempts": failed_attempts,
            "ticket": ticket,
            "refund_count": refund_count,
        },
        "checks": check_results,
        "all_passed": all_passed,
        "transcript_file": transcript_filename,
    }


def generate_transcript_md(
    path: Path,
    conv: Dict[str, Any],
    turn_results: List[Dict[str, Any]],
    session_state: Dict[str, Any],
    check_results: List[Dict[str, Any]],
    all_passed: bool,
) -> None:
    """Generates interleaved Markdown transcript file with tool calls and assertions."""
    lines = [
        f"# Evaluation Scenario {conv['id']}: {conv['title']}",
        "",
        f"- **Category:** {conv['category']}",
        f"- **Rationale:** {conv['why_chosen']}",
        f"- **Overall Status:** {'✅ PASSED' if all_passed else '❌ FAILED'}",
        f"- **Evaluated At:** {datetime.now(timezone.utc).isoformat()}",
        f"- **Model:** {GROQ_MODEL}",
        "",
        "---",
        "",
        "## Conversation Flow",
        "",
    ]

    for turn in turn_results:
        idx = turn["turn_index"]
        user_msg = turn["user_message"]
        reply = turn["reply"]
        trace = turn["trace"]

        lines.append(f"### Turn {idx}")
        lines.append(f"**Customer:** {user_msg}\n")

        # Interleave tool calls and deterministic triggers from trace
        tool_events = [t for t in trace if t.get("step") in ("tool_http_call", "deterministic_trigger", "input_guardrail")]
        if tool_events:
            lines.append("#### System & Tool Activity")
            for event in tool_events:
                step = event.get("step")
                if step == "deterministic_trigger":
                    lines.append(f"- **Deterministic Trigger:** `{event.get('trigger')}` → Ticket: `{event.get('ticket', {}).get('ticket_id')}` ({event.get('ticket', {}).get('department')}, Priority: `{event.get('ticket', {}).get('priority')}`)")
                elif step == "tool_http_call":
                    tool_name = event.get("tool")
                    req = event.get("request", {})
                    resp = event.get("response", {})
                    lines.append(f"- **Tool Call:** `{tool_name}`")
                    lines.append(f"  - Request: `{json.dumps(req)}`")
                    lines.append(f"  - Response: `{json.dumps(resp)}`")
            lines.append("")

        lines.append(f"**Paylane Assistant:** {reply}\n")

    lines.extend([
        "---",
        "",
        "## Final Session State",
        f"- **Verified:** `{session_state['verified']}`",
        f"- **Handed Off:** `{session_state['handed_off']}`",
        f"- **Failed Attempts:** `{session_state['failed_attempts']}`",
        f"- **Refunds Issued (this session):** `{session_state['refund_count']}`",
        f"- **Ticket:** `{json.dumps(session_state['ticket'])}`",
        "",
        "---",
        "",
        "## Verification Assertions",
        "",
    ])

    for c in check_results:
        box = "[x]" if c["passed"] else "[ ]"
        status_text = "PASS" if c["passed"] else "FAIL"
        lines.append(f"- {box} **{c['desc']}** (`{c['expr']}`) → **{status_text}**")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    print("=" * 70)
    print("PAYLANE SUPPORT AGENT - LIVE MODEL EVALUATION")
    print(f"Model: {GROQ_MODEL}")
    print(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 70)

    client = TestClient(app)
    results = []

    for conv in EVAL_CONVERSATIONS:
        res = run_single_conversation(client, conv)
        results.append(res)
        # Small pause between scenarios
        time.sleep(1.5)

    # Compute aggregates
    total_convs = len(results)
    passed_convs = sum(1 for r in results if r["all_passed"])
    total_checks = sum(len(r["checks"]) for r in results)
    passed_checks = sum(sum(1 for c in r["checks"] if c["passed"]) for r in results)
    conv_pass_rate = (passed_convs / total_convs) * 100 if total_convs else 0
    check_pass_rate = (passed_checks / total_checks) * 100 if total_checks else 0

    print("\n" + "=" * 70)
    print("EVALUATION SUMMARY")
    print(f"Scenarios: {passed_convs}/{total_convs} ({conv_pass_rate:.1f}%)")
    print(f"Checks:    {passed_checks}/{total_checks} ({check_pass_rate:.1f}%)")
    print("=" * 70)

    # Save results.json
    results_json_path = EVAL_DIR / "results.json"
    results_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": GROQ_MODEL,
        "total_conversations": total_convs,
        "passed_conversations": passed_convs,
        "conversation_pass_rate": conv_pass_rate,
        "total_checks": total_checks,
        "passed_checks": passed_checks,
        "check_pass_rate": check_pass_rate,
        "results": [
            {
                "id": r["id"],
                "title": r["title"],
                "category": r["category"],
                "all_passed": r["all_passed"],
                "state": r["state"],
                "checks": r["checks"],
                "transcript_file": r["transcript_file"],
            }
            for r in results
        ],
    }
    results_json_path.write_text(json.dumps(results_data, indent=2), encoding="utf-8")
    print(f"Wrote structured results to {results_json_path}")

    # Save RESULTS.md
    results_md_path = EVAL_DIR / "RESULTS.md"
    md_lines = [
        "# Paylane Support Agent – Evaluation Results",
        "",
        f"- **Latest Evaluation Run:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"- **Model:** `{GROQ_MODEL}`",
        f"- **Scenario Pass Rate:** **{passed_convs} / {total_convs}** ({conv_pass_rate:.1f}%)",
        f"- **Assertion Pass Rate:** **{passed_checks} / {total_checks}** ({check_pass_rate:.1f}%)",
        "",
        "> **Note on Architecture:** Code actions (legal, account closure, explicit human, high/normal fraud triggers, verified frozen card handoff, 3-strike lockout, and output PII redactions) are executed deterministically before or after the LLM. Tool arguments, customer verification state, and refund limits are enforced strictly by the FastAPI tool service layer.",
        "",
        "## Multi-Run Evaluation History & Live Failure Analysis",
        "",
        "| Run | Timestamp | Model | Scenarios Passed | Checks Passed | Notes / Outcome |",
        "|:---:|---|---|:---:|:---:|---|",
        "| **Run 1** | 2026-10-20 08:34:33 UTC | `openai/gpt-oss-120b` | 12 / 12 (100.0%) | 44 / 44 (100.0%) | Initial evaluation passed all scenarios. |",
        "| **Run 2** | 2026-10-20 08:44:40 UTC | `openai/gpt-oss-120b` | 11 / 12 (91.7%) | 43 / 44 (97.7%) | **Live Failure Found:** In EC_11, model answered conversationally that card was frozen but omitted calling `escalate_to_human`. |",
        f"| **Run 3 (After Fix)** | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} | `{GROQ_MODEL}` | **{passed_convs} / {total_convs}** ({conv_pass_rate:.1f}%) | **{passed_checks} / {total_checks}** ({check_pass_rate:.1f}%) | Hardened with deterministic frozen-card trigger in `graph.py` & `triggers.py`. |",
        "",
        "### Live Failure Analysis & Remediation (EC_11 Frozen Card)",
        "- **Failure Sighting (Run 2):** In EC_11, customer verified with `user2@paylane.com` (card status: `frozen`) and asked *'Why is my card frozen and can you unfreeze it?'*. The model called `get_customer`, but in the subsequent turn responded with natural language explaining the card was frozen without calling `escalate_to_human` to hand off to Security & Fraud.",
        "- **Root Cause:** LLM non-determinism. Relying on the model to call an escalation tool after recognizing card status in tool output is vulnerable to omission across turns.",
        "- **Hardened Fix:** Added deterministic frozen-card trigger in `agent/graph.py` and `agent/triggers.py`. Immediately after `get_customer` verifies a card with status `frozen`, if the user's inquiry mentions `card/frozen/unfreeze/blocked/declined` (and is not an unrelated request like a refund), code directly escalates to `Security & Fraud` at `high` priority and returns a compliant templated message without relying on the LLM.",
        "",
        "---",
        "",
        "## Summary Table (Latest Run)",
        "",
        "| ID | Scenario | Category | Checks Passed | Status | Transcript |",
        "|---|---|---|:---:|:---:|---|",
    ]

    for r in results:
        p_count = sum(1 for c in r["checks"] if c["passed"])
        t_count = len(r["checks"])
        status = "✅ PASS" if r["all_passed"] else "❌ FAIL"
        trans_link = f"[{r['transcript_file']}](../transcripts/{r['transcript_file']})"
        md_lines.append(f"| {r['id']} | {r['title']} | {r['category']} | {p_count}/{t_count} | {status} | {trans_link} |")

    md_lines.extend([
        "",
        "---",
        "",
        "## Detailed Scenario Results",
        "",
    ])

    for r in results:
        status = "✅ PASS" if r["all_passed"] else "❌ FAIL"
        md_lines.extend([
            f"### {r['id']}: {r['title']} – {status}",
            f"- **Category:** {r['category']}",
            f"- **Rationale:** {r['why_chosen']}",
            "- **Assertions:**",
        ])
        for c in r["checks"]:
            box = "[x]" if c["passed"] else "[ ]"
            res_str = "PASS" if c["passed"] else "FAIL"
            md_lines.append(f"  - {box} `{c['expr']}`: {c['desc']} (**{res_str}**)")
        md_lines.append("")

    results_md_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"Wrote markdown report to {results_md_path}")


if __name__ == "__main__":
    main()
