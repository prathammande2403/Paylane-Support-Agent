"""
agent/tools.py
Mock API implementations using Command to mutate LangGraph State directly.
"""

import random
from datetime import datetime
from typing import Annotated
from langchain_core.tools import tool, InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from langgraph.prebuilt import InjectedState
from data.mock_db import MOCK_CUSTOMERS, MOCK_TRANSACTIONS, FIXED_TEST_CLOCK, MOCK_TICKETS

def create_support_ticket(
    customer_id: str | None = None,
    summary: str = "",
    department: str = "General Support",
    priority: str = "medium"
) -> dict:
    """Helper function to create and store support tickets in MOCK_TICKETS."""
    ticket_id = f"TCK-{random.randint(10000, 99999)}"
    ticket_data = {
        "ticket_id": ticket_id,
        "customer_id": customer_id or "GUEST",
        "department": department,
        "priority": priority,
        "summary": summary,
        "status": "open",
        "created_at": FIXED_TEST_CLOCK.isoformat()
    }
    MOCK_TICKETS[ticket_id] = ticket_data
    return ticket_data

@tool
def get_customer(
    email: str,
    card_last4: str,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = ""
) -> Command:
    """Verifies customer identity by checking email and the last 4 digits of their card."""
    customer = MOCK_CUSTOMERS.get(email.lower().strip())
    
    if not customer or customer["card_last4"] != card_last4.strip():
        # Verification failed - increment failed_attempts
        failed = (state.get("failed_attempts", 0) if state else 0) + 1
        if failed >= 3:
            ticket_data = create_support_ticket(
                customer_id=customer["id"] if customer else "GUEST",
                summary="Three consecutive failed verification attempts. Account locked.",
                department="Account Operations",
                priority="high"
            )
            return Command(
                update={
                    "failed_attempts": failed,
                    "escalated": True,
                    "ticket_details": ticket_data,
                    "messages": [
                        ToolMessage(
                            content=f"Verification failed 3 times. Account actions have been locked and the case is escalated to Account Operations under ticket {ticket_data['ticket_id']}.",
                            tool_call_id=tool_call_id
                        )
                    ]
                }
            )
        
        return Command(
            update={
                "failed_attempts": failed,
                "messages": [
                    ToolMessage(
                        content=f"Verification failed: Email or card last 4 digits do not match our records. (Attempt {failed} of 3).",
                        tool_call_id=tool_call_id
                    )
                ]
            }
        )
    
    # Successful verification
    customer["verified"] = True
    return Command(
        update={
            "is_verified": True,
            "customer_id": customer["id"],
            "email_provided": email.lower().strip(),
            "card_last4_provided": card_last4.strip(),
            "failed_attempts": 0,
            "messages": [
                ToolMessage(
                    content=f"Customer successfully verified. ID: {customer['id']}, Name: {customer['name']}, Status: {customer['status']}",
                    tool_call_id=tool_call_id
                )
            ]
        }
    )

@tool
def get_transaction(
    txn_id: str,
    customer_id: str | None = None,
    state: Annotated[dict, InjectedState] = None
) -> dict:
    """Retrieves transaction details for a verified customer."""
    cust_id = customer_id or (state.get("customer_id") if state else None)
    txn = MOCK_TRANSACTIONS.get(txn_id)
    
    if not txn:
        return {"ok": False, "reason": "Transaction ID not found."}
    
    if cust_id and txn["customer_id"] != cust_id:
        return {"ok": False, "reason": "Transaction does not belong to this customer."}
    
    return {
        "ok": True,
        "txn_id": txn["txn_id"],
        "customer_id": txn["customer_id"],
        "amount": txn["amount"],
        "date": txn["date"],
        "merchant": txn["merchant"],
        "status": txn["status"]
    }

@tool
def issue_refund(
    txn_id: str,
    amount: float | None = None,
    customer_id: str | None = None,
    state: Annotated[dict, InjectedState] = None
) -> dict:
    """Attempts to issue a refund enforcing hardcoded policy guardrails."""
    cust_id = customer_id or (state.get("customer_id") if state else None)
    txn = MOCK_TRANSACTIONS.get(txn_id)
    
    if not txn:
        return {"ok": False, "reason": "Transaction not found."}
    
    if cust_id and txn["customer_id"] != cust_id:
        return {"ok": False, "reason": "Transaction does not belong to this customer."}
    
    if txn["status"] != "settled":
        return {"ok": False, "reason": f"Cannot refund transaction with status '{txn['status']}'."}
    
    refund_amount = amount if amount is not None else txn["amount"]
    
    if refund_amount > 75.00 or txn["amount"] > 75.00:
        return {"ok": False, "reason": "Refund amount exceeds $75 auto-approval limit. Must escalate to Billing & Refunds."}
    
    txn_date = datetime.fromisoformat(txn["date"])
    days_old = (FIXED_TEST_CLOCK - txn_date).days
    
    if days_old >= 30:
        return {"ok": False, "reason": f"Transaction is {days_old} days old. Exceeds 30-day refund window. Must escalate to Billing & Refunds."}
    
    txn["status"] = "refunded"
    refund_id = f"REF-{random.randint(100000, 999999)}"
    
    return {
        "ok": True,
        "refund_id": refund_id,
        "amount": refund_amount,
        "message": f"Refund successfully issued. Refund ID: {refund_id}"
    }

@tool
def escalate_to_human(
    customer_id: str | None = None,
    summary: str = "",
    department: str = "General Support",
    priority: str = "medium",
    tool_call_id: Annotated[str, InjectedToolCallId] = ""
) -> Command:
    """Escalates a customer issue to a human support agent and sets state escalated=True."""
    ticket_data = create_support_ticket(customer_id, summary, department, priority)
    return Command(
        update={
            "escalated": True,
            "ticket_details": ticket_data,
            "messages": [
                ToolMessage(
                    content=f"Escalated to {department} (Priority: {priority}). Ticket ID: {ticket_data['ticket_id']}. Summary: {summary}",
                    tool_call_id=tool_call_id
                )
            ]
        }
    )