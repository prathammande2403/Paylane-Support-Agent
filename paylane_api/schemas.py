"""
paylane_api/schemas.py
Pydantic v2 schemas for tool endpoints and demo/ops endpoints.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# --- Tool Request Schemas ---

class GetCustomerRequest(BaseModel):
    session_id: str = Field(..., description="Active session ID")
    email: str = Field(..., description="Customer email address on file")
    card_last4: str = Field(..., description="Last 4 digits of card")


class GetTransactionRequest(BaseModel):
    session_id: str = Field(..., description="Active session ID")
    txn_id: str = Field(..., description="Transaction ID (e.g. TXN_1001)")


class IssueRefundRequest(BaseModel):
    session_id: str = Field(..., description="Active session ID")
    txn_id: str = Field(..., description="Transaction ID (e.g. TXN_1001)")
    amount: Optional[float] = Field(None, description="Optional refund amount (must equal original full amount)")


class EscalateToHumanRequest(BaseModel):
    session_id: str = Field(..., description="Active session ID")
    summary: str = Field(..., description="Factual summary of the issue without PII")
    department: str = Field(..., description="Target department")
    reported_loss_usd: Optional[float] = Field(None, description="Reported dollar loss amount if applicable")


# --- Demo/Ops Request & Response Schemas ---

class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(None, description="Optional session ID. If omitted, a new session is created.")
    message: str = Field(..., max_length=1000, description="Customer message (max 1000 characters)")


class ResetRequest(BaseModel):
    session_id: Optional[str] = Field(None, description="Session ID to reset. If omitted, generates a fresh session.")


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    trace: List[Dict[str, Any]]
    state: Dict[str, Any]
    ticket: Optional[Dict[str, Any]] = None
