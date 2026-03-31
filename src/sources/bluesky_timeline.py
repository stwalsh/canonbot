"""Bluesky timeline source — wraps existing timeline poller."""

import asyncio
import os
from pathlib import Path
from typing import AsyncIterator

from src.sources.base import Source, SourceItem, SourceMode
from src.bluesky.client import BlueskyClient


class BlueskyTimelineSource(Source):
    """Polls authenticated user's Bluesky timeline."""

    def __init__(self, account: str = "", poll_interval: int = 900, **kwargs):
        self.name = "bluesky_timeline"
        self.mode = SourceMode.TRIAGE
        self.account = account
        self.poll_interval = poll_interval

    async def consume(self) -> AsyncIterator[SourceItem | None]:
        handle = os.environ.get("BSKY_HANDLE")
        password = os.environ.get("BSKY_PASSWORD")
        if not handle or not password:
            raise RuntimeError("BSKY_HANDLE and BSKY_PASSWORD must be set for timeline mode.")

        client = BlueskyClient()
        client.login(handle, password)

        # Migrate old cursor file if needed
        old_cursor = Path("data/timeline_cursor.txt")
        if old_cursor.exists() and not self._cursor_path().exists():
            self._save_cursor(old_cursor.read_text().strip())
            old_cursor.unlink()
            print("  [timeline] Migrated cursor from old location")

        cursor = self._load_cursor()
        print(f"  [timeline] Polling every {self.poll_interval}s")
        if cursor:
            print(f"  [timeline] Resuming from cursor: {cursor[:20]}...")

        empty_cycles = 0

        while True:
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(client.get_timeline, cursor=cursor, limit=50),
                    timeout=60,
                )
            except asyncio.TimeoutError:
                print("  [timeline] Timeout fetching timeline, will retry next cycle")
                await asyncio.sleep(self.poll_interval)
                continue
            except Exception as e:
                print(f"  [timeline] Error fetching timeline: {e}")
                await asyncio.sleep(self.poll_interval)
                continue

            feed = result.get("feed", [])
            new_cursor = result.get("cursor")

            if not feed:
                empty_cycles += 1
                if empty_cycles >= 3 and cursor:
                    # Stale cursor — 3 empty polls in a row, reset
                    print(f"  [timeline] Stale cursor detected ({empty_cycles} empty cycles), resetting")
                    cursor = None
                    self._save_cursor("")
                    empty_cycles = 0
                elif new_cursor:
                    cursor = new_cursor
                    self._save_cursor(cursor)
                yield None  # end-of-cycle
                await asyncio.sleep(self.poll_interval)
                continue

            empty_cycles = 0  # reset on successful feed

            for post in reversed(feed):
                text = post.get("text", "")
                if not text:
                    continue
                langs = post.get("langs", [])
                if langs and "en" not in langs:
                    continue

                yield SourceItem(
                    text=text,
                    source_name=self.name,
                    mode=self.mode,
                    author=post.get("author_handle", "") or post["author_did"],
                    uri=post["post_uri"],
                    metadata={"matched_keywords": ["timeline"]},
                )

            if new_cursor:
                cursor = new_cursor
                self._save_cursor(cursor)

            yield None  # end-of-cycle
            await asyncio.sleep(self.poll_interval)
