"""Three-stage brain: triage → retrieval → composition."""

import json
from pathlib import Path

import anthropic

from src import retriever

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "config" / "prompts"

TRIAGE_MODEL = "claude-haiku-4-5-20251001"
COMPOSITION_MODEL = "claude-sonnet-4-5-20250929"


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text().strip()


def triage(client: anthropic.Anthropic, stimulus: str) -> dict:
    """Stage 1: decide whether to engage.

    Returns {"engage": bool, "reason": str}.
    """
    system = _load_prompt("triage.md")
    response = client.messages.create(
        model=TRIAGE_MODEL,
        max_tokens=256,
        system=system,
        messages=[{"role": "user", "content": stimulus}],
    )
    text = response.content[0].text.strip()
    # Strip markdown fences if the model wraps its response
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def retrieve(stimulus: str, n_results: int = 5, exclude_ids: set[str] | None = None) -> list[dict]:
    """Stage 2: semantic search over the corpus.

    Returns list of passage dicts.
    """
    return retriever.search(stimulus, n_results=n_results, exclude_ids=exclude_ids)


# Tool schema for structured composition output
_COMPOSE_TOOL = {
    "name": "compose_response",
    "description": "Produce the bot's response to a stimulus post.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["post", "skip"],
                "description": "Whether to post or skip.",
            },
            "posts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Thread of 1-3 post texts, each max 300 characters. Empty if decision is skip.",
            },
            "passage_used": {
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "string"},
                    "poet": {"type": "string"},
                    "poem_title": {"type": "string"},
                },
                "description": "The passage that anchors the response. Null if skip.",
            },
            "skip_reason": {
                "type": "string",
                "description": "Why we're skipping. Only if decision is skip.",
            },
        },
        "required": ["decision", "posts"],
    },
}


def compose(client: anthropic.Anthropic, stimulus: str, passages: list[dict]) -> dict:
    """Stage 3: compose the bot's response using retrieved passages.

    Returns structured dict with decision, posts, passage_used, skip_reason.
    """
    system = _load_prompt("system.md")

    passages_text = "\n\n---\n\n".join(
        f"[{p['poet']} — \"{p['poem_title']}\" ({p['date']}), {p['work']}]\n{p['text']}"
        for p in passages
    )

    user_msg = (
        f"ORIGINAL POST:\n{stimulus}\n\n"
        f"RETRIEVED PASSAGES:\n{passages_text}"
    )

    response = client.messages.create(
        model=COMPOSITION_MODEL,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
        tools=[_COMPOSE_TOOL],
        tool_choice={"type": "tool", "name": "compose_response"},
    )

    # Extract the tool call input
    for block in response.content:
        if block.type == "tool_use":
            result = block.input
            # Normalize posts to list of {"text": str}
            posts = result.get("posts", [])
            if isinstance(posts, str):
                posts = [posts]
            result["posts"] = [
                p if isinstance(p, dict) else {"text": str(p)}
                for p in posts
            ]
            # Normalize passage_used
            pu = result.get("passage_used")
            if isinstance(pu, str):
                result["passage_used"] = {"chunk_id": "", "poet": "", "poem_title": pu}
            return result

    # Fallback — shouldn't happen with tool_choice forced
    return {"decision": "skip", "posts": [], "skip_reason": "No tool call in response."}


def run(client: anthropic.Anthropic, stimulus: str, exclude_ids: set[str] | None = None) -> dict:
    """Run the full triage → retrieval → composition pipeline.

    Returns:
        {
            "stimulus": str,
            "triage": {"engage": bool, "reason": str},
            "passages": [...] or None,
            "composition": {...} or None,
        }
    """
    result = {"stimulus": stimulus, "triage": None, "passages": None, "composition": None}

    # Stage 1
    result["triage"] = triage(client, stimulus)

    if not result["triage"].get("engage"):
        return result

    # Stage 2
    result["passages"] = retrieve(stimulus, exclude_ids=exclude_ids)

    # Stage 3
    result["composition"] = compose(client, stimulus, result["passages"])

    return result
