"""
cli.py
Interactive Command-Line Interface (CLI) for the Paylane AI Support Agent.
Allows chatting with the agent directly from the terminal.
"""

from __future__ import annotations
import sys
import uuid

# Ensure UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from fastapi.testclient import TestClient
from paylane_api.main import app
from paylane_api.sessions import session_store


def main():
    print("=" * 65)
    print("💳 Paylane AI Support Agent - Terminal CLI")
    print("Type your message below. Type 'exit', 'quit', or 'q' to stop.")
    print("Type 'reset' to start a fresh customer session.")
    print("=" * 65)

    client = TestClient(app)
    session_id = f"cli_{uuid.uuid4().hex[:8]}"

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting. Goodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "q"):
            print("Exiting Paylane CLI. Goodbye!")
            break

        if user_input.lower() == "reset":
            session_store.reset(session_id)
            print("[Session reset to clean initial state]")
            continue

        try:
            resp = client.post(
                "/api/chat",
                json={"session_id": session_id, "message": user_input},
            )
            if resp.status_code == 200:
                data = resp.json()
                print(f"\nAgent: {data['reply']}")
                
                # Show ticket notice if created
                if data.get("ticket"):
                    t = data["ticket"]
                    print(f"\n   [Ticket Created: {t.get('ticket_id')} | Dept: {t.get('department')} | Priority: {t.get('priority')}]")
            else:
                print(f"\n[Error {resp.status_code}]: {resp.text}")
        except Exception as e:
            print(f"\n[Execution Error]: {e}")


if __name__ == "__main__":
    main()
