"""CLI chat interface — talk to Lucubrator.

Usage:
    ./venv/bin/python -m src.chat_cli
    ./venv/bin/python -m src.chat_cli --user sean
    ./venv/bin/python -m src.chat_cli --user sean --resume
"""

import argparse
import sys

import anthropic

from src.chat import ChatSession
from src.store import Store


def main():
    parser = argparse.ArgumentParser(description="Chat with Lucubrator")
    parser.add_argument("--user", type=str, default=None, help="Your name")
    parser.add_argument("--resume", action="store_true", help="Resume last session")
    parser.add_argument("--db", type=str, default=None)
    args = parser.parse_args()

    user_name = args.user
    if not user_name:
        try:
            user_name = input("Who's here? ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not user_name:
            user_name = "someone"

    client = anthropic.Anthropic()
    store = Store(db_path=args.db) if args.db else Store()

    session_id = None
    if args.resume:
        prev = store.get_latest_chat_session(user_name)
        if prev:
            session_id = prev["id"]
            print(f"  Resuming session {session_id}.")

    session = ChatSession(client, store, user_name, session_id=session_id)

    # Greeting
    print()
    greeting = session.greeting()
    print(f"  {greeting}")
    print()

    # REPL
    try:
        while True:
            try:
                user_input = input(f"  {user_name}: ")
            except EOFError:
                break
            if not user_input.strip():
                continue
            if user_input.strip().lower() in ("quit", "exit", "/quit", "/exit"):
                break

            result = session.respond(user_input.strip())

            # Show response
            print()
            model_tag = " [opus]" if result["escalated"] else ""
            print(f"  lucu{model_tag}: {result['text']}")
            print()

    except KeyboardInterrupt:
        pass

    print("\n  [session ended]")
    store.close()


if __name__ == "__main__":
    main()
