"""Multiplexer — merges multiple source async generators into a single stream."""

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator

from src.sources.base import Source, SourceItem


@dataclass
class MuxItem:
    """Wrapper tracking which source produced the item."""
    item: SourceItem | None  # None = end-of-cycle sentinel
    source: Source


async def multiplex(sources: list[Source]) -> AsyncIterator[MuxItem]:
    """Merge multiple source async generators into a single stream.

    Uses one asyncio.Task per source. Yields from whichever source is ready first.
    When a source is exhausted (StopAsyncIteration), it's removed from the pool.
    """
    if not sources:
        return

    # Start a consumer iterator for each source
    iters = {s: s.consume().__aiter__() for s in sources}
    pending: dict[asyncio.Task, Source] = {}

    def _schedule(source: Source):
        it = iters[source]
        task = asyncio.create_task(it.__anext__())
        pending[task] = source

    # Schedule initial fetch for all sources
    for source in sources:
        _schedule(source)

    while pending:
        done, _ = await asyncio.wait(pending.keys(), return_when=asyncio.FIRST_COMPLETED)

        for task in done:
            source = pending.pop(task)
            try:
                item = task.result()
                yield MuxItem(item=item, source=source)
                # Re-schedule this source for its next item
                _schedule(source)
            except StopAsyncIteration:
                # Source exhausted — don't re-schedule
                pass
            except Exception as e:
                # Source errored — log and re-schedule to keep trying
                print(f"  [mux] Error from {source.name}: {e}")
                _schedule(source)
