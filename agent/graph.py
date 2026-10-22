"""
agent/graph.py
LangGraph agent state machine for Paylane Support Agent.
Enforces deterministic pre-checks, input/output guardrails, single retry mechanism,
and pure session-backed identity and verification rules.
"""

from __future__ import annotations
import os
import time
import re
from typing import Annotated, Any, Dict, List, Optional, TypedDict
from uuid import uuid4
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
from agent.triggers import (
    check_deterministic_triggers,
    check_frozen_card_trigger,
    check_pending_fraud_trigger,
    check_refund_escalation_trigger,
    check_verification_retry,
    parse_reported_dollar_amounts,
    extract_transaction_id,
)
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
    locked: bool


tools = [get_customer, get_transaction, issue_refund, escalate_to_human]

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


def get_human_texts(messages: List[Any]) -> List[str]:
    texts: List[str] = []
    for msg in messages:
        if isinstance(msg, tuple) and len(msg) == 2 and msg[0] in ("user", "human"):
            texts.append(str(msg[1]))
        elif isinstance(msg, dict) and msg.get("role") in ("user", "human"):
            texts.append(str(msg.get("content", "")))
        elif getattr(msg, "type", None) == "human":
            texts.append(str(getattr(msg, "content", "")))
    return texts


def agent_node(state: PaylaneState, config: Optional[RunnableConfig] = None) -> Dict[str, Any]:
    session_id = _get_session_id(config)
    session = session_store.get_or_create(session_id)

    last_msg = state["messages"][-1]
    is_tool_response = (
        isinstance(last_msg, ToolMessage)
        or (hasattr(last_msg, "type") and last_msg.type == "tool")
    )

    if not is_tool_response:
        # Locked session guard: if 3 failed attempts or locked, block the chat immediately
        if getattr(session, "locked", False) or getattr(session, "failed_attempts", 0) >= 3:
            locked_reply = (
                "This chat session has been locked due to 3 failed verification attempts. "
                "For security reasons, no further messages or actions can be processed in this session."
            )
            session.trace.append({"step": "locked_chat_blocked", "reply": locked_reply})
            return {
                "messages": [AIMessage(content=locked_reply)],
                "escalated": False,
                "ticket_details": None,
                "is_verified": False,
                "customer_id": session.customer_id,
                "failed_attempts": session.failed_attempts,
                "locked": True,
            }

        user_text = get_last_user_message(state["messages"])

        # Input Guardrail
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

        # Check if pending_fraud is waiting for matching transaction details after verification
        if getattr(session, "verified", False) and getattr(session, "pending_fraud", None):
            email_match = re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", user_text)
            card_match = re.search(r"\b\d{4}\b", user_text)
            if email_match and card_match:
                from paylane_api.service import verify_customer
                v_res = verify_customer(session, email_match.group(0), card_match.group(0))
                if not v_res["ok"]:
                    if getattr(session, "locked", False):
                        reply = (
                            "Verification failed 3 times. This chat session has been locked for security reasons. "
                            "All further actions are blocked."
                        )
                        return {
                            "messages": [AIMessage(content=reply)],
                            "escalated": False,
                            "ticket_details": None,
                            "is_verified": False,
                            "customer_id": session.customer_id,
                            "failed_attempts": session.failed_attempts,
                            "locked": True,
                        }
                    else:
                        return {
                            "messages": [AIMessage(content=v_res["reason"])],
                            "escalated": False,
                            "ticket_details": None,
                            "is_verified": False,
                            "customer_id": session.customer_id,
                            "failed_attempts": session.failed_attempts,
                        }

            fraud_res = check_pending_fraud_trigger(session, user_text=user_text)
            if fraud_res:
                session.trace.append({
                    "step": "deterministic_trigger",
                    "trigger": fraud_res["trigger"],
                    "ticket": fraud_res.get("ticket"),
                })
                sanitized_reply = sanitize_output_pii(fraud_res["reply"])
                session.trace.append({"step": "final_reply", "reply": sanitized_reply})
                return {
                    "messages": [AIMessage(content=sanitized_reply)],
                    "escalated": session.handed_off or bool(fraud_res.get("ticket")),
                    "ticket_details": session.ticket,
                    "is_verified": session.verified,
                    "customer_id": session.customer_id,
                    "failed_attempts": session.failed_attempts,
                    "locked": getattr(session, "locked", False),
                }

        # Intercept Fraud intent early if unverified
        fraud_pattern = r"\b(fraud|unauthorized|stolen|didn't make this charge|unrecognised charge|unrecognized charge)\b"
        if re.search(fraud_pattern, user_text.lower()) and not session.verified:
            loss_usd = parse_reported_dollar_amounts(user_text)
            txn_id = extract_transaction_id(user_text)
            session.pending_fraud = {"loss_usd": loss_usd, "txn_id": txn_id}
            
            reply = (
                "I'm sorry to hear about this unauthorized charge. To protect your account, "
                "I must verify your identity first. Please provide your account email address and the last 4 digits of your card."
            )
            session.trace.append({"step": "final_reply", "reply": reply})
            return {
                "messages": [AIMessage(content=reply)],
                "escalated": session.handed_off,
                "ticket_details": session.ticket,
                "is_verified": session.verified,
                "customer_id": session.customer_id,
                "failed_attempts": session.failed_attempts,
            }

        # Deterministic Emergency Triggers
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
                "escalated": session.handed_off,
                "ticket_details": session.ticket,
                "is_verified": session.verified,
                "customer_id": session.customer_id,
                "failed_attempts": session.failed_attempts,
            }

        # Deterministic Frozen Card Check
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

        # Deterministic verification retry
        retry_args = check_verification_retry(user_text, get_human_texts(state["messages"]), session)
        if retry_args:
            session.trace.append({
                "step": "forced_verification",
                "card_last4": retry_args["card_last4"],
            })
            return {
                "messages": [AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "get_customer",
                        "args": retry_args,
                        "id": f"call_{uuid4().hex[:12]}",
                        "type": "tool_call",
                    }],
                )],
                "escalated": session.handed_off,
                "ticket_details": session.ticket,
                "is_verified": session.verified,
                "customer_id": session.customer_id,
                "failed_attempts": session.failed_attempts,
            }

    # Deterministic refund escalation
    if is_tool_response and getattr(last_msg, "name", None) == "issue_refund":
        refund_res = check_refund_escalation_trigger(session)
        if refund_res:
            session.trace.append({
                "step": "deterministic_trigger",
                "trigger": refund_res["trigger"],
                "ticket": refund_res["ticket"],
            })
            sanitized_reply = sanitize_output_pii(refund_res["reply"])
            session.trace.append({"step": "final_reply", "reply": sanitized_reply})
            return {
                "messages": [AIMessage(content=sanitized_reply)],
                "escalated": True,
                "ticket_details": refund_res["ticket"],
                "is_verified": session.verified,
                "customer_id": session.customer_id,
                "failed_attempts": session.failed_attempts,
            }

    # Deterministic check immediately after get_customer tool response
    if is_tool_response:
        tool_name = getattr(last_msg, "name", None)
        if tool_name == "get_customer" or any(
            isinstance(m, ToolMessage) and getattr(m, "name", "") == "get_customer"
            for m in state["messages"][-3:]
        ):
            if getattr(session, "locked", False) or getattr(session, "failed_attempts", 0) >= 3:
                reply = (
                    "Verification failed 3 times. This chat session has been locked for security reasons. "
                    "All further actions are blocked."
                )
                session.trace.append({"step": "locked_chat_blocked", "reply": reply})
                return {
                    "messages": [AIMessage(content=reply)],
                    "escalated": False,
                    "ticket_details": None,
                    "is_verified": False,
                    "customer_id": session.customer_id,
                    "failed_attempts": session.failed_attempts,
                    "locked": True,
                }

            user_text = get_last_user_message(state["messages"])
            fraud_res = check_pending_fraud_trigger(session, user_text=user_text) or check_frozen_card_trigger(user_text, session)
            if fraud_res:
                session.trace.append({
                    "step": "deterministic_trigger",
                    "trigger": fraud_res["trigger"],
                    "ticket": fraud_res.get("ticket"),
                })
                sanitized_reply = sanitize_output_pii(fraud_res["reply"])
                session.trace.append({"step": "final_reply", "reply": sanitized_reply})
                return {
                    "messages": [AIMessage(content=sanitized_reply)],
                    "escalated": session.handed_off or bool(fraud_res.get("ticket")),
                    "ticket_details": session.ticket,
                    "is_verified": session.verified,
                    "customer_id": session.customer_id,
                    "failed_attempts": session.failed_attempts,
                }

    # Build system prompt
    system_prompt = PAYLANE_SYSTEM_PROMPT
    if session.handed_off:
        system_prompt += POST_HANDOFF_ADDENDUM

    full_prompt = [SystemMessage(content=system_prompt)] + list(state["messages"])

    # LLM invoke
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

    if hasattr(response, "content") and isinstance(response.content, str):
        response.content = sanitize_output_pii(response.content)

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