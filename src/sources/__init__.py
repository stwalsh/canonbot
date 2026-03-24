"""Unified source system — config-driven multi-source stimulus/seed architecture."""

from src.sources.base import Source, SourceItem, SourceMode
from src.sources.multiplexer import multiplex, MuxItem
from src.sources.seeds import SeedAccumulator

# Source implementations
from src.sources.bluesky_timeline import BlueskyTimelineSource
from src.sources.bluesky_firehose import BlueskyFirehoseSource
from src.sources.feed_file import FeedFileSource
from src.sources.stimuli_dir import StimuliDirSource

_SOURCE_BUILDERS = {
    "bluesky_timeline": BlueskyTimelineSource,
    "bluesky_firehose": BlueskyFirehoseSource,
    "feed_file": FeedFileSource,
    "stimuli_dir": StimuliDirSource,
}


def _try_import_rss():
    try:
        from src.sources.rss import RSSSource
        _SOURCE_BUILDERS["rss"] = RSSSource
    except ImportError:
        pass  # feedparser not installed


_try_import_rss()


def build_sources(source_configs: list[dict]) -> list[Source]:
    """Instantiate sources from config dicts.

    Each config dict must have 'type' and optionally 'mode'.
    All other keys are passed to the source constructor.
    """
    sources = []
    for cfg in source_configs:
        cfg = dict(cfg)  # don't mutate original
        source_type = cfg.pop("type")
        if source_type not in _SOURCE_BUILDERS:
            print(f"  [sources] Unknown source type: {source_type}, skipping")
            continue
        cls = _SOURCE_BUILDERS[source_type]
        # Mode override from config (source classes set their own defaults)
        if "mode" in cfg:
            mode_str = cfg.pop("mode")
            source = cls(**cfg)
            source.mode = SourceMode(mode_str)
        else:
            source = cls(**cfg)
        sources.append(source)
        print(f"  [sources] Registered: {source.name} ({source.mode.value})")
    return sources


__all__ = [
    "Source", "SourceItem", "SourceMode",
    "MuxItem", "multiplex",
    "SeedAccumulator",
    "build_sources",
]
