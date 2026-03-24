"""Feed file source — watches a single file for changes, yields content as seed."""

import asyncio
from pathlib import Path
from typing import AsyncIterator

from src.sources.base import Source, SourceItem, SourceMode


class FeedFileSource(Source):
    """Watches a file for mtime changes. Yields full content as a seed when modified."""

    def __init__(self, path: str, name: str = "", poll_interval: int = 300, **kwargs):
        self.path = Path(path)
        self.name = name or f"feed_file:{self.path.stem}"
        self.mode = SourceMode.SEED
        self.poll_interval = poll_interval
        self._last_mtime: float = 0

    async def consume(self) -> AsyncIterator[SourceItem | None]:
        while True:
            if self.path.exists():
                mtime = self.path.stat().st_mtime
                if mtime > self._last_mtime:
                    self._last_mtime = mtime
                    text = self.path.read_text(encoding="utf-8")
                    if text.strip():
                        yield SourceItem(
                            text=text,
                            source_name=self.name,
                            mode=self.mode,
                            author="feed_file",
                            uri=str(self.path),
                        )
            yield None  # end-of-cycle
            await asyncio.sleep(self.poll_interval)
