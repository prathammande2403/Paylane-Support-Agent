"""
tests/run_evals.py
Headless runner script to execute test conversations through the LangGraph agent,
perform pass/fail assertions, save transcripts, and output evaluation scoreboard.
"""
import sys
import os

# Ensure root directory is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Ensure UTF-8 output encoding if supported by stdout
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import agent.config  
import json
from datetime import datetime, timezone
from agent.graph import paylane_graph
from data.mock_db import reset_mock_db
from eval.conversations import TEST_CONVERSATIONS

TRANSCRIPT_DIR = "transcripts"

def run_evaluation():
    os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
    
    total_tests = len(TEST_CONVERSATIONS)
    passed_tests = 0
    results_summary = []
    
    print(f"=== Starting Paylane Support Agent Evaluation ({total_tests} test cases) ===\n")
    
    for test in TEST_CONVERSATIONS:
        test_id = test["id"]
        test_name = test["name"]
        expected = test["expected_outcome"]
        
        print(f"Running [{test_id}] {test_name}...")
        
        # Reset mock database to clean state before each test
        reset_mock_db()
        
        # Initialize graph state for this conversation session
        state = {
            "messages": [],
            "customer_id": None,
            "email_provided": None,
            "card_last4_provided": None,
            "is_verified": False,
            "failed_attempts": 0,
            "unresolved_turns": 0,
            "escalated": False,
            "ticket_details": None
        }
        
        conversation_transcript = []
        
        # Feed each message in the test script into the graph
        for user_msg in test["messages"]:
            conversation_transcript.append({"role": "user", "content": user_msg})
            state["messages"].append(("user", user_msg))
            
            # Run graph step
            result = paylane_graph.invoke(state)
            state = result
            
            # Extract latest assistant message
            latest_msg = state["messages"][-1]
            assistant_content = latest_msg.content if hasattr(latest_msg, "content") else str(latest_msg)
            conversation_transcript.append({"role": "assistant", "content": assistant_content})
        
        # Evaluate outcome against expectations
        passed = True
        failure_reason = ""
        
        transcript_text = " ".join(msg.get("content", "") for msg in conversation_transcript)
        transcript_text_lower = transcript_text.lower()
        
        if expected == "verified_and_refunded":
            if not state.get("is_verified") or not any("REF-" in msg.get("content", "") for msg in conversation_transcript):
                passed = False
                failure_reason = "Expected customer verification and successful refund ID generation."
        elif "escalated" in expected:
            if not state.get("escalated"):
                passed = False
                failure_reason = "Expected agent to escalate issue, but escalation flag is false."
        elif "refused" in expected or "rejected" in expected:
            if state.get("escalated") and "REF-" in transcript_text:
                passed = False
                failure_reason = "Expected agent to refuse/reject request without processing."
        elif expected == "asked_clarifying_question":
            if not state.get("is_verified") or "transaction id" not in transcript_text_lower:
                passed = False
                failure_reason = "Expected agent to verify customer and ask for missing transaction ID."
        
        if passed:
            passed_tests += 1
            print(f"  -> PASSED [OK]")
        else:
            print(f"  -> FAILED [X] ({failure_reason})")
            
        results_summary.append({
            "test_id": test_id,
            "name": test_name,
            "passed": passed,
            "failure_reason": failure_reason,
            "transcript": conversation_transcript
        })
        
        # Save individual transcript
        transcript_path = os.path.join(TRANSCRIPT_DIR, f"{test_id}_transcript.json")
        with open(transcript_path, "w", encoding="utf-8") as f:
            json.dump({
                "test_id": test_id,
                "name": test_name,
                "passed": passed,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "transcript": conversation_transcript
            }, f, indent=2)
            
    score_percentage = (passed_tests / total_tests) * 100
    print(f"\n========================================")
    print(f" EVALUATION COMPLETE: {passed_tests}/{total_tests} Passed ({score_percentage:.1f}%)")
    print(f" Transcripts saved to '{TRANSCRIPT_DIR}/'")
    print(f"========================================")

if __name__ == "__main__":
    run_evaluation()