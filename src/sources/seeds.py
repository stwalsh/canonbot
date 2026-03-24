"""Seed accumulator — collects seed items for injection into composition context."""

from src.sources.base import SourceItem


class SeedAccumulator:
    """Collects the latest seed from each seed source.

    Seeds are replaced (not appended) when a source yields a new item.
    """

    MAX_CHARS = 4000  # cap total seed string to avoid bloating composition context

    def __init__(self):
        self._seeds: dict[str, SourceItem] = {}

    def update(self, item: SourceItem):
        """Store or replace seed from this source."""
        self._seeds[item.source_name] = item

    def as_context_string(self) -> str | None:
        """Format all seeds for injection into reflection_context."""
        if not self._seeds:
            return None
        parts = []
        for name, item in self._seeds.items():
            parts.append(f"=== SEED: {name} ===\n{item.text}")
        result = "\n\n".join(parts)
        if len(result) > self.MAX_CHARS:
            result = result[:self.MAX_CHARS] + "\n[... truncated]"
        return result
