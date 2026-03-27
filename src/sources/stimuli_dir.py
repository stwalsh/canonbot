"""Stimuli directory source — watches a folder for new/modified files."""

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

from src.sources.base import Source, SourceItem, SourceMode


class StimuliDirSource(Source):
    """Watches a directory for .md/.txt files. Yields each as a seed.

    Persists processed file state so restarts don't re-trigger old stimuli.
    """

    def __init__(self, path: str, name: str = "", poll_interval: int = 300, **kwargs):
        self.path = Path(path)
        self.name = name or f"stimuli_dir:{self.path.name}"
        self.mode = SourceMode.SEED
        self.poll_interval = poll_interval
        self._known: dict[str, float] = {}  # filename -> mtime

    def _load_known(self):
        """Load processed file state from cursor."""
        cursor = self._load_cursor()
        if cursor:
            try:
                self._known = json.loads(cursor)
            except (json.JSONDecodeError, TypeError):
                pass

    def _save_known(self):
        """Persist processed file state."""
        self._save_cursor(json.dumps(self._known))

    async def consume(self) -> AsyncIterator[SourceItem | None]:
        self._load_known()

        while True:
            changed = False
            if self.path.is_dir():
                for f in sorted(self.path.glob("*.md")) + sorted(self.path.glob("*.txt")):
                    if f.name.startswith("."):
                        continue
                    mtime = f.stat().st_mtime
                    if f.name not in self._known or mtime > self._known[f.name]:
                        self._known[f.name] = mtime
                        changed = True
                        text = f.read_text(encoding="utf-8")
                        if text.strip():
                            yield SourceItem(
                                text=text,
                                source_name=f"{self.name}:{f.stem}",
                                mode=self.mode,
                                author="stimuli_dir",
                                uri=str(f),
                            )
            if changed:
                self._save_known()
            yield None  # end-of-cycle
            await asyncio.sleep(self.poll_interval)
