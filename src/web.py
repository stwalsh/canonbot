"""Lightweight local test UI for the brain pipeline."""

from pathlib import Path

import anthropic
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from src.engine import Engine

load_dotenv()

TEMPLATE = (Path(__file__).parent / "templates" / "index.html").read_text()
ENGINE = Engine()


async def index(request: Request) -> HTMLResponse:
    return HTMLResponse(TEMPLATE)


async def submit(request: Request) -> JSONResponse:
    body = await request.json()
    stimulus = body.get("stimulus", "").strip()
    if not stimulus:
        return JSONResponse({"error": "Empty stimulus"}, status_code=400)
    result = await run_in_threadpool(
        ENGINE.process, stimulus, source="web_ui",
    )
    return JSONResponse(result)


app = Starlette(
    routes=[
        Route("/", index),
        Route("/submit", submit, methods=["POST"]),
    ],
)
