"""Multiplexer — merges multiple source async generators into a single stream."""

import asyncio
from dataclasses import dataclass, field
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
    Sources that error get exponential backoff (5s, 10s, 20s... max 5min).
    """
    if not sources:
        return

    # Start a consumer iterator for each source
    iters = {s: s.consume().__aiter__() for s in sources}
    pending: dict[asyncio.Task, Source] = {}
    error_counts: dict[str, int] = {}  # source.name -> consecutive error count

    def _schedule(source: Source):
        it = iters[source]
        task = asyncio.create_task(it.__anext__())
        pending[task] = source

    async def _schedule_after_backoff(source: Source, errors: int):
        delay = min(5 * (2 ** (errors - 1)), 300)  # 5s, 10s, 20s... max 5min
        print(f"  [mux] {source.name}: backing off {delay}s after {errors} errors")
        await asyncio.sleep(delay)
        _schedule(source)

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
                error_counts.pop(source.name, None)  # reset on success
                _schedule(source)
            except StopAsyncIteration:
                # Source exhausted — don't re-schedule
                pass
            except Exception as e:
                count = error_counts.get(source.name, 0) + 1
                error_counts[source.name] = count
                print(f"  [mux] Error from {source.name} (attempt {count}): {e}")
                if count >= 10:
                    print(f"  [mux] {source.name}: too many errors, removing source")
                else:
                    asyncio.create_task(_schedule_after_backoff(source, count))
