"""
app.py
Interactive Streamlit Web UI for the Paylane AI Support Agent.
Includes a live chat playground, real-time session state inspector, and ticket queue.
"""

import streamlit as st
import sys
import os

# Add root directory to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from agent.graph import paylane_graph
from data.mock_db import MOCK_TICKETS, reset_mock_db

st.set_page_config(
    page_title="Paylane AI Support Agent",
    page_icon="💳",
    layout="wide"
)

st.title("💳 Paylane AI Support Agent & Operations Dashboard")
st.markdown(
    "Production-grade fintech customer support agent built with **LangGraph**, "
    "deterministic Python tool guardrails, and pre-flight intake filtering."
)

# Initialize session state for chat
if "messages" not in st.session_state:
    st.session_state.messages = []
if "graph_state" not in st.session_state:
    st.session_state.graph_state = {
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

# Sidebar: Live State Inspector
st.sidebar.header("🔍 Live Session State Inspector")
state = st.session_state.graph_state
st.sidebar.metric("Customer ID", state.get("customer_id") or "None")
st.sidebar.metric("Verified Status", "✅ Verified" if state.get("is_verified") else "❌ Unverified")
st.sidebar.metric("Failed Attempts", state.get("failed_attempts", 0))
st.sidebar.metric("Unresolved Turns", state.get("unresolved_turns", 0))
st.sidebar.metric("Escalated to Human", "🚨 Yes" if state.get("escalated") else "No")

if state.get("ticket_details"):
    st.sidebar.subheader("Active Ticket Info")
    st.sidebar.json(state.get("ticket_details"))

if st.sidebar.button("🔄 Reset Chat Session & Database", use_container_width=True):
    reset_mock_db()
    st.session_state.messages = []
    st.session_state.graph_state = {
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
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption("⚡ Powered by LangGraph & Groq LLM")

# Main Tabs
tab1, tab2 = st.tabs(["💬 Live Chat Playground", "📋 Ticket Escalation Queue"])

with tab1:
    st.subheader("Chat with Paylane Support")
    
    # Display chat history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            
    # User input
    if prompt := st.chat_input("Type your message to Paylane support..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
            
        # Update graph state messages
        st.session_state.graph_state["messages"].append(("user", prompt))
        
        with st.chat_message("assistant"):
            with st.spinner("Agent processing..."):
                try:
                    # Invoke graph
                    result = paylane_graph.invoke(st.session_state.graph_state)
                    st.session_state.graph_state = result
                    
                    # Get last assistant response
                    latest_msg = result["messages"][-1]
                    assistant_content = latest_msg.content if hasattr(latest_msg, "content") else str(latest_msg)
                    
                    st.markdown(assistant_content)
                    st.session_state.messages.append({"role": "assistant", "content": assistant_content})
                    st.rerun()
                except Exception as e:
                    error_msg = f"A temporary issue occurred while processing your request: {e}. Please try sending your message again."
                    st.error(error_msg)
                    st.session_state.messages.append({"role": "assistant", "content": error_msg})

with tab2:
    st.subheader("Active Support Tickets / Escalation Queue")
    if MOCK_TICKETS:
        for tck_id, tck in MOCK_TICKETS.items():
            with st.expander(f"🎫 {tck_id} - {tck.get('department')} ({tck.get('priority').upper()})", expanded=True):
                col1, col2, col3 = st.columns(3)
                col1.write(f"**Customer ID:** `{tck.get('customer_id')}`")
                col2.write(f"**Priority:** `{tck.get('priority')}`")
                col3.write(f"**Status:** `{tck.get('status')}`")
                st.write(f"**Summary:** {tck.get('summary')}")
                st.caption(f"Created at: {tck.get('created_at')}")
        with st.expander("Raw Tickets JSON"):
            st.json(MOCK_TICKETS)
    else:
        st.info("No tickets escalated yet in this session.")