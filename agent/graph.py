"""
agent/graph.py
LangGraph agent state machine for Paylane Support Agent.
Enforces deterministic pre-checks, input/output guardrails, single retry mechanism,
and pure session-backed identity and verification rules.
"""

from __future__ import annotations
import os
import time
from typing import Annotated, Any, Dict, List, Optional, TypedDict
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_groq import ChatGroq
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from agent.config import GROQ_MODEL, GROQ_API_KEY
from agent.guardrails import check_input_guardrails, sanitize_output_pii
from agent.prompt import PAYLANE_SYSTEM_PROMPT, POST_HANDOFF_ADDENDUM
from agent.tools import (
    escalate_to_human,
    get_customer,
    get_transaction,
    issue_refund,
    _get_session_id,
)
from agent.triggers import check_deterministic_triggers, check_frozen_card_trigger
from paylane_api.sessions import session_store


class PaylaneState(TypedDict, total=False):
    messages: Annotated[List[Any], add_messages]
    customer_id: Optional[str]
    email_provided: Optional[str]
    card_last4_provided: Optional[str]
    is_verified: bool
    failed_attempts: int
    escalated: bool
    ticket_details: Optional[Dict[str, Any]]


tools = [get_customer, get_transaction, issue_refund, escalate_to_human]

# ChatGroq configured with max_retries=0 to ensure single retry mechanism in code
llm = ChatGroq(
    model=GROQ_MODEL,
    api_key=GROQ_API_KEY,
    temperature=0,
    max_retries=0,
).bind_tools(tools)


def get_last_user_message(messages: List[Any]) -> str:
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "human":
            return str(msg.content)
        if isinstance(msg, HumanMessage):
            return str(msg.content)
        if isinstance(msg, tuple) and len(msg) == 2 and msg[0] in ("user", "human"):
            return str(msg[1])
        if isinstance(msg, dict) and msg.get("role") in ("user", "human"):
            return str(msg.get("content", ""))
    return ""


def agent_node(state: PaylaneState, config: Optional[RunnableConfig] = None) -> Dict[str, Any]:
    session_id = _get_session_id(config)
    session = session_store.get_or_create(session_id)

    last_msg = state["messages"][-1]
    is_tool_response = (
        isinstance(last_msg, ToolMessage)
        or (hasattr(last_msg, "type") and last_msg.type == "tool")
    )

    # 1. On new human messages: run input guardrails and deterministic emergency triggers
    if not is_tool_response:
        user_text = get_last_user_message(state["messages"])

        # Input Guardrail (cheap jailbreak backstop)
        guard_res = check_input_guardrails(user_text)
        session.trace.append({
            "step": "input_guardrail",
            "safe": guard_res["safe"],
            "reason": guard_res.get("reason"),
        })
        if not guard_res["safe"]:
            sanitized_reply = sanitize_output_pii(guard_res["message"])
            session.trace.append({"step": "final_reply", "reply": sanitized_reply})
            return {
                "messages": [AIMessage(content=sanitized_reply)],
                "escalated": session.handed_off,
                "ticket_details": session.ticket,
                "is_verified": session.verified,
                "customer_id": session.customer_id,
                "failed_attempts": session.failed_attempts,
            }

        # Deterministic Emergency Triggers (Legal, Account Closure, Human, Fraud)
        trigger_res = check_deterministic_triggers(user_text, session)
        if trigger_res:
            session.trace.append({
                "step": "deterministic_trigger",
                "trigger": trigger_res["trigger"],
                "ticket": trigger_res["ticket"],
            })
            sanitized_reply = sanitize_output_pii(trigger_res["reply"])
            session.trace.append({"step": "final_reply", "reply": sanitized_reply})
            return {
                "messages": [AIMessage(content=sanitized_reply)],
                "escalated": True,
                "ticket_details": trigger_res["ticket"],
                "is_verified": session.verified,
                "customer_id": session.customer_id,
                "failed_attempts": session.failed_attempts,
            }

        # Deterministic Frozen Card Check (if already verified)
        frozen_res = check_frozen_card_trigger(user_text, session)
        if frozen_res:
            session.trace.append({
                "step": "deterministic_trigger",
                "trigger": frozen_res["trigger"],
                "ticket": frozen_res["ticket"],
            })
            sanitized_reply = sanitize_output_pii(frozen_res["reply"])
            session.trace.append({"step": "final_reply", "reply": sanitized_reply})
            return {
                "messages": [AIMessage(content=sanitized_reply)],
                "escalated": True,
                "ticket_details": frozen_res["ticket"],
                "is_verified": session.verified,
                "customer_id": session.customer_id,
                "failed_attempts": session.failed_attempts,
            }

    # Deterministic check immediately after successful get_customer tool response
    if is_tool_response:
        tool_name = getattr(last_msg, "name", None)
        if tool_name == "get_customer" or any(
            isinstance(m, ToolMessage) and getattr(m, "name", "") == "get_customer"
            for m in state["messages"][-3:]
        ):
            user_text = get_last_user_message(state["messages"])
            frozen_res = check_frozen_card_trigger(user_text, session)
            if frozen_res:
                session.trace.append({
                    "step": "deterministic_trigger",
                    "trigger": frozen_res["trigger"],
                    "ticket": frozen_res["ticket"],
                })
                sanitized_reply = sanitize_output_pii(frozen_res["reply"])
                session.trace.append({"step": "final_reply", "reply": sanitized_reply})
                return {
                    "messages": [AIMessage(content=sanitized_reply)],
                    "escalated": True,
                    "ticket_details": frozen_res["ticket"],
                    "is_verified": session.verified,
                    "customer_id": session.customer_id,
                    "failed_attempts": session.failed_attempts,
                }

    # 2. Build system prompt reading session state directly
    system_prompt = PAYLANE_SYSTEM_PROMPT
    if session.handed_off:
        system_prompt += POST_HANDOFF_ADDENDUM

    full_prompt = [SystemMessage(content=system_prompt)] + list(state["messages"])

    # 3. LLM invoke with single exponential backoff retry mechanism (max 4 tries for 429s and connection errors)
    response = None
    for attempt in range(4):
        try:
            response = llm.invoke(full_prompt, config=config)
            break
        except Exception as e:
            err_str = str(e).lower()
            is_retryable = any(k in err_str for k in ("429", "rate limit", "connection", "timeout", "econnreset"))
            if is_retryable and attempt < 3:
                time.sleep(2.0 * (2 ** attempt))
            else:
                raise e

    # 4. Output PII Masking
    if hasattr(response, "content") and isinstance(response.content, str):
        response.content = sanitize_output_pii(response.content)

    # Record trace
    if hasattr(response, "tool_calls") and response.tool_calls:
        for tc in response.tool_calls:
            session.trace.append({
                "step": "llm_tool_call",
                "name": tc.get("name"),
                "args": tc.get("args"),
            })
    else:
        session.trace.append({
            "step": "final_reply",
            "reply": response.content if hasattr(response, "content") else str(response),
        })

    return {
        "messages": [response],
        "escalated": session.handed_off,
        "ticket_details": session.ticket,
        "is_verified": session.verified,
        "customer_id": session.customer_id,
        "failed_attempts": session.failed_attempts,
    }


def should_continue(state: PaylaneState) -> str:
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return END


workflow = StateGraph(PaylaneState)
workflow.add_node("agent", agent_node)
workflow.add_node("tools", ToolNode(tools))

workflow.add_edge(START, "agent")
workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
workflow.add_edge("tools", "agent")

paylane_graph = workflow.compile()