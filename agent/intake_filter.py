"""
agent/intake_filter.py
Pre-flight validation middleware for Paylane support agent.
Filters out non-existent users, guests without purchases, or expired support windows.
"""

from datetime import datetime, timezone
from data.mock_db import MOCK_CUSTOMERS, MOCK_TRANSACTIONS, FIXED_TEST_CLOCK

def run_intake_filter(email: str | None, txn_id: str | None = None) -> dict:
    """
    Validates incoming requests before passing them to the LangGraph agent.
    
    Returns:
        dict: {"allowed": bool, "reason": str, "customer_id": str | None}
    """
    if not email:
        # Allow general inquiries or guest chats (like asking about policies)
        return {
            "allowed": True,
            "reason": "General inquiry permitted without pre-flight ID.",
            "customer_id": None
        }
    
    clean_email = email.lower().strip()
    customer = MOCK_CUSTOMERS.get(clean_email)
    
    # Check 1: Does the user exist in the database?
    if not customer:
        return {
            "allowed": False,
            "reason": "Email address not found in Paylane records. Please sign up or contact general support.",
            "customer_id": None
        }
    
    customer_id = customer["id"]
    
    # Check 2: If a specific transaction is requested, validate global support window (< 30 days)
    if txn_id:
        txn = MOCK_TRANSACTIONS.get(txn_id)
        if txn:
            if txn["customer_id"] != customer_id:
                return {
                    "allowed": False,
                    "reason": "Transaction does not belong to this account.",
                    "customer_id": customer_id
                }
            
            txn_date = datetime.fromisoformat(txn["date"])
            days_old = (FIXED_TEST_CLOCK - txn_date).days
            
            # Global support time limit check (e.g., past 30 days cannot be disputed automatically)
            if days_old > 365:
                return {
                    "allowed": False,
                    "reason": f"Transaction is {days_old} days old. Beyond the 1-year maximum archival support window.",
                    "customer_id": customer_id
                }
    
    return {
        "allowed": True,
        "reason": "Intake filter passed successfully.",
        "customer_id": customer_id
    }