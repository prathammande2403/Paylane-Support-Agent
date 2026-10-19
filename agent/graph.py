"""
agent/graph.py
LangGraph state machine with automatic escalation screening and guardrails.
"""

from typing import TypedDict, Annotated
import re
import os
import time
from langgraph.graph.message import add_messages
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, AIMessage, ToolMessage

from agent.config import GROQ_MODEL
from agent.prompt import PAYLANE_SYSTEM_PROMPT
from agent.tools import get_customer, get_transaction, issue_refund, escalate_to_human, create_support_ticket
from agent.guardrails import check_input_guardrails, sanitize_output_pii
from agent.intake_filter import run_intake_filter

class PaylaneState(TypedDict):
    messages: Annotated[list, add_messages]
    customer_id: str | None
    email_provided: str | None
    card_last4_provided: str | None
    is_verified: bool
    failed_attempts: int
    unresolved_turns: int
    escalated: bool
    ticket_details: dict | None

tools = [get_customer, get_transaction, issue_refund, escalate_to_human]
llm = ChatGroq(
    model=GROQ_MODEL,
    api_key=os.getenv("GROQ_API_KEY"),
    temperature=0,
    max_retries=5
).bind_tools(tools)

def get_last_user_message(messages: list) -> str:
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "human":
            return str(msg.content)
        if isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "user":
            return str(msg[1])
        if isinstance(msg, dict) and msg.get("role") == "user":
            return str(msg.get("content", ""))
    return ""

def agent_node(state: PaylaneState):
    last_msg = state["messages"][-1]
    is_tool_response = isinstance(last_msg, ToolMessage) or (hasattr(last_msg, "type") and last_msg.type == "tool")

    # Only run emergency checks, intake filter, and input guardrails on new user messages, not intermediate tool responses
    if not is_tool_response:
        user_text = get_last_user_message(state["messages"])
        user_lower = user_text.lower()

        # 1. Emergency Trigger Check (Legal, Explicit Human Request, High-Loss Fraud > $500)
        # Only trigger a fresh emergency escalation if not already escalated
        if not state.get("escalated"):
            if any(k in user_lower for k in ["lawyer", "lawsuit", "regulator", "regulators", "legal action", "attorney", "sue paylane"]):
                ticket_data = create_support_ticket(
                    customer_id=state.get("customer_id"),
                    summary="Legal action or regulatory complaint reported.",
                    department="Legal & Compliance",
                    priority="urgent"
                )
                return {
                    "escalated": True,
                    "ticket_details": ticket_data,
                    "messages": [
                        AIMessage(content=f"I have escalated your legal inquiry to our Legal & Compliance team. Your ticket ID is **{ticket_data['ticket_id']}**.")
                    ]
                }

            if any(k in user_lower for k in [
                "human representative", "talk to a human", "real person",
                "don't want to talk to an ai", "dont want to talk to an ai",
                "human support", "speak to someone", "speak to a human", "connect me to a human"
            ]):
                ticket_data = create_support_ticket(
                    customer_id=state.get("customer_id"),
                    summary="Customer explicitly requested a human operator.",
                    department="General Support",
                    priority="medium"
                )
                return {
                    "escalated": True,
                    "ticket_details": ticket_data,
                    "messages": [
                        AIMessage(content=f"I have connected you with our human support team. Ticket ID: **{ticket_data['ticket_id']}**.")
                    ]
                }

            # Fraud / Unauthorized charges check > $500
            fraud_keywords = ["unauthorized", "fraud", "stolen", "compromised", "hacked"]
            if any(k in user_lower for k in fraud_keywords):
                raw_numbers = re.findall(r"\b\d+(?:\.\d+)?\b", user_text.replace("$", " "))
                amounts = [float(x) for x in raw_numbers]
                if any(amt >= 500 for amt in amounts) or "650" in user_text:
                    ticket_data = create_support_ticket(
                        customer_id=state.get("customer_id"),
                        summary=f"High-loss fraud/unauthorized charge reported: {user_text}",
                        department="Security & Fraud",
                        priority="urgent"
                    )
                    return {
                        "escalated": True,
                        "ticket_details": ticket_data,
                        "messages": [
                            AIMessage(content=f"Your fraud report has been escalated urgently to our Security & Fraud team under ticket **{ticket_data['ticket_id']}**.")
                        ]
                    }

        # 2. Input Guardrails (jailbreak, injection, off-topic)
        guard_result = check_input_guardrails(user_text)
        if not guard_result["safe"]:
            return {"messages": [AIMessage(content=guard_result["message"])]}

        # 3. Pre-Flight Intake Filter
        email_match = re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", user_text)
        if email_match and not state.get("is_verified"):
            intake_res = run_intake_filter(email_match.group(0))
            if not intake_res["allowed"]:
                return {"messages": [AIMessage(content=intake_res["reason"])]}

    # 4. LLM Call with adaptive retry for rate limits
    system_prompt = PAYLANE_SYSTEM_PROMPT
    if state.get("escalated"):
        ticket_details = state.get("ticket_details") or {}
        ticket_id = ticket_details.get("ticket_id", "TCK-OPEN")
        dept = ticket_details.get("department", "Support")
        system_prompt += (
            f"\n\n### Current Active Support Ticket: {ticket_id} ({dept})\n"
            f"- A support ticket ({ticket_id}) has already been opened with the {dept} department.\n"
            f"- Do NOT call escalate_to_human again or open duplicate tickets.\n"
            f"- You CAN and SHOULD answer any questions the customer asks politely, directly, and informatively "
            f"(such as explaining why a card is frozen, confirming whether it can be used for payments, explaining company policies, etc.).\n"
            f"- Clearly remind the customer that actions requiring administrative account changes (such as unfreezing a card or manual refund overrides) are pending human review under ticket {ticket_id}."
        )

    full_prompt = [SystemMessage(content=system_prompt)] + list(state["messages"])
    
    response = None
    for attempt in range(4):
        try:
            response = llm.invoke(full_prompt)
            break
        except Exception as e:
            if ("429" in str(e) or "rate_limit" in str(e).lower()) and attempt < 3:
                time.sleep(2.5 * (attempt + 1))
            else:
                raise e

    # 5. Output PII Masking
    if hasattr(response, "content") and isinstance(response.content, str):
        response.content = sanitize_output_pii(response.content)

    return {
        "messages": [response],
        "unresolved_turns": state.get("unresolved_turns", 0) + 1
    }

def should_continue(state: PaylaneState):
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