"""RSS/Atom feed source — polls a feed URL for new entries."""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import AsyncIterator

try:
    import feedparser
except ImportError:
    feedparser = None

from src.sources.base import Source, SourceItem, SourceMode


class RSSSource(Source):
    """Polls an RSS/Atom feed. Yields new entries since last seen."""

    def __init__(
        self,
        url: str,
        name: str = "",
        poll_interval: int = 3600,
        **kwargs,
    ):
        if feedparser is None:
            raise ImportError("feedparser is required for RSS sources: pip install feedparser")
        self.url = url
        slug = hashlib.md5(url.encode()).hexdigest()[:8]
        self.name = name or f"rss:{slug}"
        self.mode = SourceMode.SEED  # default, overridden by config
        self.poll_interval = poll_interval

    async def consume(self) -> AsyncIterator[SourceItem | None]:
        seen_ids = set()

        # Load cursor (set of seen entry IDs)
        cursor = self._load_cursor()
        if cursor:
            try:
                seen_ids = set(json.loads(cursor))
            except (json.JSONDecodeError, TypeError):
                pass

        print(f"  [rss:{self.name}] Polling {self.url} every {self.poll_interval}s")

        while True:
            try:
                feed = await asyncio.to_thread(feedparser.parse, self.url)
            except Exception as e:
                print(f"  [rss:{self.name}] Error fetching feed: {e}")
                await asyncio.sleep(self.poll_interval)
                continue

            new_entries = []
            for entry in feed.entries:
                entry_id = entry.get("id") or entry.get("link") or entry.get("title", "")
                if entry_id in seen_ids:
                    continue
                seen_ids.add(entry_id)
                new_entries.append(entry)

            for entry in new_entries:
                # Build text from title + summary/content
                parts = []
                title = entry.get("title", "")
                if title:
                    parts.append(title)
                # Prefer content over summary
                content = ""
                if entry.get("content"):
                    content = entry.content[0].get("value", "")
                elif entry.get("summary"):
                    content = entry.summary
                if content:
                    parts.append(content)

                text = "\n\n".join(parts)
                if not text.strip():
                    continue

                yield SourceItem(
                    text=text,
                    source_name=self.name,
                    mode=self.mode,
                    author=entry.get("author", feed.feed.get("title", "")),
                    uri=entry.get("link", ""),
                )

            # Persist cursor — cap at 500 most recent IDs
            if len(seen_ids) > 500:
                # Keep the IDs we just saw (most recent) plus as many old ones as fit
                recent = [entry.get("id") or entry.get("link") or entry.get("title", "")
                          for entry in feed.entries]
                recent_set = set(recent)
                old = [sid for sid in seen_ids if sid not in recent_set]
                # Recent first, then old — trim from the old end
                ordered = recent + old
                seen_ids = set(ordered[:500])
            self._save_cursor(json.dumps(list(seen_ids)))

            yield None  # end-of-cycle
            await asyncio.sleep(self.poll_interval)
