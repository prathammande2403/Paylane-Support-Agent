"""
agent/graph.py
LangGraph agent state machine for Paylane Support Agent.
Enforces deterministic pre-checks, input/output guardrails, single retry mechanism,
and strict tool-calling flows preventing unauthorized or invalid escalations.
"""

from __future__ import annotations
import os
import time
import re
import json
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

    # =========================================================================
    # PART A: POST-TOOL RESPONSE VALIDATION & HARD STOP GUARDS
    # =========================================================================
    if is_tool_response:
        tool_name = getattr(last_msg, "name", None)
        
        # 1. STRICT TRANSACTION FAILURE GUARD: If get_transaction failed, block escalation immediately!
        if tool_name == "get_transaction":
            try:
                content_dict = json.loads(last_msg.content) if isinstance(last_msg.content, str) else last_msg.content
                if isinstance(content_dict, dict) and not content_dict.get("ok", True):
                    session.trace.append({
                        "step": "blocked_invalid_transaction",
                        "reason": "get_transaction returned not found, blocking escalation"
                    })
                    return {
                        "messages": [AIMessage(content="I couldn't verify that transaction ID in our system. Please check the transaction ID and provide a valid one before we can proceed with an escalation or refund.")],
                        "escalated": False,
                        "is_verified": session.verified,
                        "customer_id": session.customer_id,
                        "failed_attempts": session.failed_attempts,
                    }
            except Exception:
                pass

        # 2. POST-VERIFICATION & LOCKOUT CHECKS
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

    # =========================================================================
    # PART B: NON-TOOL RESPONSE / USER MESSAGE FLOWS
    # =========================================================================
    if not is_tool_response:
        # 1. LOCKED SESSION GUARD
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

        # 2. INPUT GUARDRAIL
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

        # 3. FORCE GET_CUSTOMER TOOL CALL IF CREDENTIALS ARE PROVIDED
        email_match = re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", user_text)
        card_match = re.search(r"\b\d{4}\b", user_text)
        if email_match and card_match and not session.verified:
            session.trace.append({
                "step": "forced_verification_tool_call",
                "email": email_match.group(0),
                "card_last4": card_match.group(0),
            })
            return {
                "messages": [AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "get_customer",
                        "args": {
                            "email": email_match.group(0),
                            "card_last4": card_match.group(0)
                        },
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

        # 4. FRAUD INTENT CHECK (Unverified)
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

        # 5. DETERMINISTIC EMERGENCY TRIGGERS (Strictly block unverified ticket creation)
        trigger_res = check_deterministic_triggers(user_text, session)
        if trigger_res:
            if not session.verified:
                reply = (
                    "I understand this is frustrating, but I cannot create a support or legal ticket for unverified users. "
                    "For your security, please provide your account email address and the last 4 digits of your card so we can verify your identity first."
                )
                session.trace.append({"step": "unverified_trigger_blocked", "reply": reply})
                return {
                    "messages": [AIMessage(content=reply)],
                    "escalated": False,
                    "ticket_details": None,
                    "is_verified": False,
                    "customer_id": session.customer_id,
                    "failed_attempts": session.failed_attempts,
                }

            session.trace.append({
                "step": "forced_trigger_tool_call",
                "trigger": trigger_res["trigger"],
            })
            return {
                "messages": [AIMessage(
                    content=trigger_res["reply"],
                    tool_calls=[{
                        "name": "escalate_to_human",
                        "args": {
                            "department": trigger_res.get("department", "General Support"),
                            "summary": f"Emergency trigger fired: {trigger_res['trigger']} - {user_text}",
                            "reported_loss_usd": None
                        },
                        "id": f"call_{uuid4().hex[:12]}",
                        "type": "tool_call",
                    }],
                )],
                "escalated": True,
                "ticket_details": session.ticket,
                "is_verified": session.verified,
                "customer_id": session.customer_id,
                "failed_attempts": session.failed_attempts,
            }

        # 6. FROZEN CARD CHECK
        frozen_res = check_frozen_card_trigger(user_text, session)
        if frozen_res:
            if not session.verified:
                reply = (
                    "To check your card status or escalate card issues, I must verify your identity first. "
                    "Please provide your account email address and the last 4 digits of your card."
                )
                return {
                    "messages": [AIMessage(content=reply)],
                    "escalated": False,
                    "is_verified": False,
                    "customer_id": session.customer_id,
                    "failed_attempts": session.failed_attempts,
                }

            session.trace.append({
                "step": "forced_frozen_tool_call",
                "trigger": frozen_res["trigger"],
            })
            return {
                "messages": [AIMessage(
                    content=frozen_res["reply"],
                    tool_calls=[{
                        "name": "escalate_to_human",
                        "args": {
                            "department": "Security & Fraud",
                            "summary": f"Frozen card inquiry by verified user: {user_text}",
                            "reported_loss_usd": None
                        },
                        "id": f"call_{uuid4().hex[:12]}",
                        "type": "tool_call",
                    }],
                )],
                "escalated": True,
                "ticket_details": session.ticket,
                "is_verified": session.verified,
                "customer_id": session.customer_id,
                "failed_attempts": session.failed_attempts,
            }

        # 7. VERIFICATION RETRY BACKSTOP
        retry_args = check_verification_retry(user_text, get_human_texts(state["messages"]), session)
        if retry_args:
            session.trace.append({
                "step": "forced_verification_retry",
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

    # =========================================================================
    # PART C: REFUND POLICY ESCALATION GUARD
    # =========================================================================
    if is_tool_response and getattr(last_msg, "name", None) == "issue_refund":
        refund_res = check_refund_escalation_trigger(session)
        if refund_res:
            session.trace.append({
                "step": "forced_escalation_tool_call",
                "reason": "Refund failed policy limits, forcing escalate_to_human tool execution"
            })
            return {
                "messages": [AIMessage(
                    content="I cannot approve this refund automatically. I am routing this to our Billing & Refunds team for review.",
                    tool_calls=[{
                        "name": "escalate_to_human",
                        "args": {
                            "department": "Billing & Refunds",
                            "summary": f"Refund request failed policy validation: {refund_res.get('reason', 'Policy limit exceeded')}",
                            "reported_loss_usd": None
                        },
                        "id": f"call_{uuid4().hex[:12]}",
                        "type": "tool_call",
                    }],
                )],
                "escalated": True,
                "ticket_details": refund_res.get("ticket"),
                "is_verified": session.verified,
                "customer_id": session.customer_id,
                "failed_attempts": session.failed_attempts,
            }

    # =========================================================================
    # PART D: DEFAULT LLM CONVERSATIONAL REASONING LAYER
    # =========================================================================
    system_prompt = PAYLANE_SYSTEM_PROMPT
    if session.handed_off:
        system_prompt += POST_HANDOFF_ADDENDUM

    full_prompt = [SystemMessage(content=system_prompt)] + list(state["messages"])

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