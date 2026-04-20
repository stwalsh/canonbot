"""Web chat interface — lightweight Starlette app.

Usage:
    ./venv/bin/python -m src.chat_web
    ./venv/bin/python -m src.chat_web --port 8800
"""

import argparse
import sys
from pathlib import Path

import anthropic
import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from src.chat import ChatSession
from src.store import Store

load_dotenv()

TEMPLATE = (Path(__file__).parent / "templates" / "chat.html").read_text()

_client = anthropic.Anthropic()
_store = Store()

# Active sessions keyed by session_id
_sessions: dict[int, ChatSession] = {}


async def index(request: Request) -> HTMLResponse:
    return HTMLResponse(TEMPLATE)


async def start(request: Request) -> JSONResponse:
    body = await request.json()
    user_name = (body.get("user_name") or "someone").strip()

    session = ChatSession(_client, _store, user_name)
    _sessions[session.session_id] = session

    greeting = await run_in_threadpool(session.greeting)

    return JSONResponse({
        "session_id": session.session_id,
        "greeting": greeting,
    })


async def send(request: Request) -> JSONResponse:
    body = await request.json()
    session_id = body.get("session_id")
    message = (body.get("message") or "").strip()

    if not session_id or not message:
        return JSONResponse({"error": "Missing session_id or message"}, status_code=400)

    session = _sessions.get(session_id)
    if not session:
        # Try to resurrect from DB
        sess_data = _store.get_chat_session(session_id)
        if not sess_data:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        session = ChatSession(
            _client, _store, sess_data["user_name"], session_id=session_id
        )
        _sessions[session_id] = session

    result = await run_in_threadpool(session.respond, message)

    return JSONResponse({
        "text": result["text"],
        "model": result["model"],
        "escalated": result["escalated"],
    })


app = Starlette(
    routes=[
        Route("/", index),
        Route("/chat/start", start, methods=["POST"]),
        Route("/chat/send", send, methods=["POST"]),
    ],
)


def main():
    parser = argparse.ArgumentParser(description="Lucubrator chat web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8800)
    args = parser.parse_args()

    print(f"  Lucubrator chat → http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
