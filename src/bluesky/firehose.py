"""Jetstream WebSocket consumer — filtered firehose of Bluesky posts."""

import json
import re

import websockets
import yaml

_DEFAULT_CONFIG = {
    "jetstream_url": "wss://jetstream1.us-east.bsky.network/subscribe",
    "firehose_keywords": [
        "death", "grief", "memory", "ambition", "failure", "power",
        "language", "silence", "war", "beauty", "time", "solitude",
        "god", "hypocrisy", "authority", "corruption",
    ],
}


def _load_bluesky_config() -> dict:
    try:
        with open("config/config.yaml") as f:
            cfg = yaml.safe_load(f)
        return {**_DEFAULT_CONFIG, **(cfg.get("bluesky") or {})}
    except FileNotFoundError:
        return _DEFAULT_CONFIG


def _build_keyword_pattern(keywords: list[str]) -> re.Pattern:
    """Build a compiled regex that matches any keyword as a whole word."""
    escaped = [re.escape(k) for k in keywords]
    return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)


async def consume(config: dict | None = None):
    """Async generator yielding matching posts from the Jetstream firehose.

    Yields dicts with keys: text, author_did, post_uri, matched_keywords.
    """
    config = config or _load_bluesky_config()
    url = config["jetstream_url"]
    keywords = config["firehose_keywords"]
    pattern = _build_keyword_pattern(keywords)

    params = "wantedCollections=app.bsky.feed.post"
    ws_url = f"{url}?{params}"

    print(f"  [firehose] Connecting to {ws_url}")
    print(f"  [firehose] Filtering for {len(keywords)} keywords")

    async for ws in websockets.connect(ws_url, ping_interval=30, ping_timeout=10):
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                # Only care about create events
                if msg.get("kind") != "commit":
                    continue
                commit = msg.get("commit", {})
                if commit.get("operation") != "create":
                    continue
                if commit.get("collection") != "app.bsky.feed.post":
                    continue

                record = commit.get("record", {})
                text = record.get("text", "")
                if not text:
                    continue

                # Language filter: prefer English, skip if explicitly non-English
                langs = record.get("langs", [])
                if langs and "en" not in langs:
                    continue

                # Keyword match
                matches = pattern.findall(text)
                if not matches:
                    continue

                did = msg.get("did", "")
                rkey = commit.get("rkey", "")
                post_uri = f"at://{did}/app.bsky.feed.post/{rkey}" if did and rkey else ""

                yield {
                    "text": text,
                    "author_did": did,
                    "post_uri": post_uri,
                    "matched_keywords": list(set(m.lower() for m in matches)),
                }

        except websockets.ConnectionClosed:
            print("  [firehose] Connection closed, reconnecting...")
            continue
