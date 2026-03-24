"""Bluesky firehose source — wraps existing firehose consumer."""

from typing import AsyncIterator

from src.sources.base import Source, SourceItem, SourceMode
from src.bluesky.firehose import consume as firehose_consume


class BlueskyFirehoseSource(Source):
    """Jetstream firehose with keyword filtering. Continuous — no end-of-cycle."""

    def __init__(self, keywords: list[str] | None = None, **kwargs):
        self.name = "bluesky_firehose"
        self.mode = SourceMode.TRIAGE
        self.keywords = keywords

    async def consume(self) -> AsyncIterator[SourceItem | None]:
        async for post in firehose_consume():
            yield SourceItem(
                text=post["text"],
                source_name=self.name,
                mode=self.mode,
                author=post.get("author_handle", "") or post["author_did"],
                uri=post["post_uri"],
                metadata={"matched_keywords": post.get("matched_keywords", [])},
            )
