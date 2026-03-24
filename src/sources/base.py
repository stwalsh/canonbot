"""Base types for the unified source system."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import AsyncIterator


class SourceMode(Enum):
    TRIAGE = "triage"  # items go through triage -> retrieval -> composition
    SEED = "seed"      # items injected into composition context as enrichment


@dataclass
class SourceItem:
    """A single item from any source."""
    text: str
    source_name: str
    mode: SourceMode
    author: str = ""
    uri: str = ""
    metadata: dict = field(default_factory=dict)


class Source:
    """Base class for all stimulus/seed sources.

    Subclasses implement consume() as an async generator yielding SourceItem.
    Yield None to signal end of a poll cycle (for polled sources).
    """

    name: str = "unknown"
    mode: SourceMode = SourceMode.TRIAGE

    def _cursor_path(self) -> Path:
        return Path("data/source_cursors") / f"{self.name}.txt"

    def _load_cursor(self) -> str | None:
        p = self._cursor_path()
        if p.exists():
            text = p.read_text().strip()
            return text if text else None
        return None

    def _save_cursor(self, cursor: str):
        p = self._cursor_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(cursor)

    async def consume(self) -> AsyncIterator[SourceItem | None]:
        """Yield SourceItems. Yield None at end of a poll cycle."""
        raise NotImplementedError
        yield  # make this a generator

    async def close(self):
        """Cleanup on shutdown."""
        pass
