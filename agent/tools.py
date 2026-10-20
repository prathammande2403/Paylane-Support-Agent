"""
agent/tools.py
LangChain tool wrappers that expose ONLY model-visible arguments to the LLM.
Session ID is injected from LangGraph configurable context.
Identity is never accepted from the model.
Calls FastAPI service endpoints via in-process ASGI transport or PAYLANE_API_URL.
"""

from __future__ import annotations
import os
from typing import Any, Dict, Optional
import httpx
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig


def _get_session_id(config: Optional[RunnableConfig]) -> str:
    """Extracts session_id from LangGraph RunnableConfig, defaulting safely."""
    if not config:
        return "default_session"
    configurable = config.get("configurable", {}) if isinstance(config, dict) else getattr(config, "configurable", {})
    return configurable.get("thread_id", "default_session")


def _call_tool_api(endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calls FastAPI tool endpoint using httpx or in-process TestClient.
    Uses real HTTP if PAYLANE_API_URL is set, otherwise in-process TestClient.
    """
    api_url = os.getenv("PAYLANE_API_URL")
    if api_url:
        with httpx.Client(base_url=api_url.rstrip("/"), timeout=15.0) as client:
            resp = client.post(endpoint, json=payload)
            return resp.json()
    else:
        from fastapi.testclient import TestClient
        from paylane_api.main import app
        with TestClient(app, base_url="http://testserver") as client:
            resp = client.post(endpoint, json=payload)
            return resp.json()


@tool
def get_customer(
    email: str,
    card_last4: str,
    config: RunnableConfig = None,
) -> Dict[str, Any]:
    """
    Verifies customer identity by checking both the email address and the last 4 digits of their card.
    Both must match our records. Verification state is stored securely in session state.
    """
    session_id = _get_session_id(config)
    payload = {
        "session_id": session_id,
        "email": email,
        "card_last4": card_last4,
    }
    return _call_tool_api("/tools/get_customer", payload)


@tool
def get_transaction(
    txn_id: str,
    config: RunnableConfig = None,
) -> Dict[str, Any]:
    """
    Retrieves transaction details for a verified customer.
    Customer must be verified before calling this tool.
    """
    session_id = _get_session_id(config)
    payload = {
        "session_id": session_id,
        "txn_id": txn_id,
    }
    return _call_tool_api("/tools/get_transaction", payload)


@tool
def issue_refund(
    txn_id: str,
    amount: Optional[float] = None,
    config: RunnableConfig = None,
) -> Dict[str, Any]:
    """
    Attempts to issue a refund enforcing company policy rules.
    Auto-approved only if verified, settled, <= $75, < 30 days old, and not refunded before.
    Only full original transaction amount can be refunded.
    """
    session_id = _get_session_id(config)
    payload = {
        "session_id": session_id,
        "txn_id": txn_id,
        "amount": amount,
    }
    return _call_tool_api("/tools/issue_refund", payload)


@tool
def escalate_to_human(
    department: str,
    summary: str,
    reported_loss_usd: Optional[float] = None,
    config: RunnableConfig = None,
) -> Dict[str, Any]:
    """
    Escalates an issue to a human support agent and creates a support ticket.
    Department must be one of: Billing & Refunds, Security & Fraud, Legal & Compliance,
    Account Operations, General Support.
    Priority is determined automatically by the system.
    Summary must contain facts only without emails or card numbers.
    """
    session_id = _get_session_id(config)
    payload = {
        "session_id": session_id,
        "summary": summary,
        "department": department,
        "reported_loss_usd": reported_loss_usd,
    }
    return _call_tool_api("/tools/escalate_to_human", payload)