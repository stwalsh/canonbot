"""Timeline poller — polls authenticated user's feed at a configurable interval.

Async generator, same yield interface as firehose.py:
    {text, author_did, author_handle, post_uri, matched_keywords}

Unlike the firehose, no keyword filtering — the follow list IS the filter.
matched_keywords is always ["timeline"] for compatibility with runner logging.
"""

import asyncio
import os
from pathlib import Path

import yaml

from src.bluesky.client import BlueskyClient

CURSOR_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "timeline_cursor.txt"

_DEFAULT_POLL_INTERVAL = 900  # 15 minutes


def _load_poll_interval() -> int:
    try:
        with open("config/config.yaml") as f:
            cfg = yaml.safe_load(f)
        return (cfg.get("bluesky") or {}).get(
            "timeline_poll_interval_seconds", _DEFAULT_POLL_INTERVAL
        )
    except FileNotFoundError:
        return _DEFAULT_POLL_INTERVAL


def _load_cursor() -> str | None:
    if CURSOR_FILE.exists():
        text = CURSOR_FILE.read_text().strip()
        return text if text else None
    return None


def _save_cursor(cursor: str):
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(cursor)


async def consume(config: dict | None = None):
    """Async generator yielding posts from the authenticated user's timeline.

    Yields dicts with keys: text, author_did, author_handle, post_uri, matched_keywords.
    Polls at the configured interval, persists cursor across restarts.
    """
    handle = os.environ.get("BSKY_HANDLE")
    password = os.environ.get("BSKY_PASSWORD")
    if not handle or not password:
        raise RuntimeError("BSKY_HANDLE and BSKY_PASSWORD must be set for timeline mode.")

    poll_interval = _load_poll_interval()
    if config:
        poll_interval = config.get("timeline_poll_interval_seconds", poll_interval)

    client = BlueskyClient()
    client.login(handle, password)

    cursor = _load_cursor()
    print(f"  [timeline] Polling every {poll_interval}s")
    if cursor:
        print(f"  [timeline] Resuming from cursor: {cursor[:20]}...")

    while True:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(client.get_timeline, cursor=cursor, limit=50),
                timeout=60,
            )
        except asyncio.TimeoutError:
            print("  [timeline] Timeout fetching timeline, will retry next cycle")
            await asyncio.sleep(poll_interval)
            continue
        except Exception as e:
            print(f"  [timeline] Error fetching timeline: {e}")
            await asyncio.sleep(poll_interval)
            continue

        feed = result.get("feed", [])
        new_cursor = result.get("cursor")

        if not feed:
            if new_cursor:
                cursor = new_cursor
                _save_cursor(cursor)
            yield {"_end_of_cycle": True}
            await asyncio.sleep(poll_interval)
            continue

        # Yield posts (newest first from API, but we process in order)
        for post in reversed(feed):
            text = post.get("text", "")
            if not text:
                continue

            # Language filter: prefer English
            langs = post.get("langs", [])
            if langs and "en" not in langs:
                continue

            yield {
                "text": text,
                "author_did": post["author_did"],
                "author_handle": post.get("author_handle", ""),
                "post_uri": post["post_uri"],
                "matched_keywords": ["timeline"],
            }

        if new_cursor:
            cursor = new_cursor
            _save_cursor(cursor)

        # Signal end of poll cycle to runner
        yield {"_end_of_cycle": True}

        await asyncio.sleep(poll_interval)
