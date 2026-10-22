"""
paylane_api/main.py
FastAPI application for Paylane Support Agent.
Exposes Swagger tool endpoints, demo/ops chat endpoints, health check,
and serves the standalone demo UI at GET /.
NOTE: paylane_graph is imported lazily to avoid circular import with agent.tools.
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from langchain_core.messages import HumanMessage

from paylane_api.schemas import (
    GetCustomerRequest,
    GetTransactionRequest,
    IssueRefundRequest,
    EscalateToHumanRequest,
    ChatRequest,
    ChatResponse,
    ResetRequest,
)
from paylane_api.service import (
    verify_customer,
    get_transaction,
    issue_refund,
    escalate_to_human,
)
from paylane_api.sessions import session_store

app = FastAPI(
    title="Paylane Support Agent API",
    description="Tool execution service and chat interface for Paylane Support Agent.",
    version="1.0.0",
)

# CORS: Strict configuration (no wildcard)
allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
allowed_origins = [orig.strip() for orig in allowed_origins_env.split(",") if orig.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def _check_ip_rate_limit(request: Request):
    client_ip = request.client.host if request.client else "127.0.0.1"
    if not session_store.check_rate_limit(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded (max 20 requests per minute). Please try again shortly.",
        )


# --- Tool Endpoints ---

@app.post("/tools/get_customer", summary="Verify Customer Credentials")
def api_get_customer(req: GetCustomerRequest, request: Request) -> Dict[str, Any]:
    _check_ip_rate_limit(request)
    session = session_store.get_or_create(req.session_id)
    res = verify_customer(session, req.email, req.card_last4)
    session.trace.append({
        "step": "tool_http_call",
        "tool": "get_customer",
        "request": req.model_dump(),
        "response": res,
    })
    return res


@app.post("/tools/get_transaction", summary="Retrieve Transaction Details")
def api_get_transaction(req: GetTransactionRequest, request: Request) -> Dict[str, Any]:
    _check_ip_rate_limit(request)
    session = session_store.get_or_create(req.session_id)
    res = get_transaction(session, req.txn_id)
    session.trace.append({
        "step": "tool_http_call",
        "tool": "get_transaction",
        "request": req.model_dump(),
        "response": res,
    })
    return res


@app.post("/tools/issue_refund", summary="Issue Eligible Refund")
def api_issue_refund(req: IssueRefundRequest, request: Request) -> Dict[str, Any]:
    _check_ip_rate_limit(request)
    session = session_store.get_or_create(req.session_id)
    res = issue_refund(session, req.txn_id, amount=req.amount)
    session.trace.append({
        "step": "tool_http_call",
        "tool": "issue_refund",
        "request": req.model_dump(),
        "response": res,
    })
    return res


@app.post("/tools/escalate_to_human", summary="Escalate Issue to Human Support")
def api_escalate_to_human(req: EscalateToHumanRequest, request: Request) -> Dict[str, Any]:
    _check_ip_rate_limit(request)
    session = session_store.get_or_create(req.session_id)
    try:
        res = escalate_to_human(
            session,
            summary=req.summary,
            department=req.department,
            reported_loss_usd=req.reported_loss_usd,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    session.trace.append({
        "step": "tool_http_call",
        "tool": "escalate_to_human",
        "request": req.model_dump(),
        "response": res,
    })
    return res


# --- Demo / Ops Endpoints ---

@app.post("/api/chat", response_model=ChatResponse, summary="Process Agent Chat Turn")
def api_chat(req: ChatRequest, request: Request) -> ChatResponse:
    """Process one chat turn through the LangGraph agent."""
    _check_ip_rate_limit(request)

    # Message length cap
    if len(req.message) > 1000:
        raise HTTPException(status_code=400, detail="Message too long (max 1000 characters).")

    session = session_store.get_or_create(req.session_id)

    # Maintain conversation messages in session (lazy init)
    if not hasattr(session, "messages"):
        session.messages = []

    session.messages.append(HumanMessage(content=req.message))

    # Lazy import to break circular dependency
    from agent.graph import paylane_graph

    # Invoke LangGraph agent with session_id in configurable
    config = {"configurable": {"thread_id": session.session_id}}
    result = paylane_graph.invoke({"messages": session.messages}, config=config)
    session.messages = result["messages"]

    latest_msg = result["messages"][-1]
    reply = latest_msg.content if hasattr(latest_msg, "content") else str(latest_msg)

    return ChatResponse(
        session_id=session.session_id,
        reply=reply,
        trace=session.trace,
        state={
            "verified": session.verified,
            "failed_attempts": session.failed_attempts,
            "handed_off": session.handed_off,
            "customer_id": session.customer_id,
            "locked": getattr(session, "locked", False),
        },
        ticket=session.ticket,
    )


@app.get("/api/sessions/{session_id}", summary="Get Session State and Full Trace")
def api_get_session(session_id: str, request: Request) -> Dict[str, Any]:
    _check_ip_rate_limit(request)
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    return session.to_dict()


@app.get("/api/tickets/{session_id}", summary="Get Ticket for Session")
def api_get_ticket(session_id: str, request: Request) -> Dict[str, Any]:
    _check_ip_rate_limit(request)
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    return {"ticket": session.ticket}


@app.post("/api/reset", summary="Reset Session")
def api_reset(req: Optional[ResetRequest] = None, request: Request = None) -> Dict[str, Any]:
    sid = req.session_id if req else None
    new_session = session_store.reset(sid)
    return {"session_id": new_session.session_id, "status": "reset"}


@app.get("/health", summary="Health Check")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "paylane-support-agent"}


# --- UI Endpoint ---

@app.get("/", response_class=HTMLResponse, summary="Paylane Demo UI")
def serve_ui():
    ui_path = Path(__file__).resolve().parent.parent / "static" / "index.html"
    if ui_path.exists():
        return FileResponse(str(ui_path))
    return HTMLResponse("<h2>Paylane Demo UI not found</h2>", status_code=404)
