# Lucubrator

A literary critic whose intelligence is shaped by the English poetry canon (c. 1250–1900). It writes long-form critical prose, thinking *through* the poetry — surfacing specific passages when they illuminate whatever's been put in front of it.

Published at [stwalsh.github.io/lucubrator](https://stwalsh.github.io/lucubrator). 3-5 editorially revised pieces per day, plus a curated daily notebook.

## What it does

You clip an article, an essay, a poem, a lyric — anything — and send it to the bot. It retrieves relevant passages from a 70,000-chunk poetry corpus, writes 2-3 paragraphs of critical prose exploring the collision between the stimulus and the canon, and an editorial pass catches formulaic habits before publication. It also self-generates: meditating on single passages, comparing two passages, or writing unprompted long-form essays.

## Architecture

```
src/
  runner.py              Unified source runner (multiplexes all inputs)
  brain.py               Pipeline: triage, composition (Opus), reflection, revision
  engine.py              Orchestrator: rate limits, poet cooling, editorial review
  retriever.py           ChromaDB semantic search
  store.py               SQLite: interactions, reflections, readings, passage notes
  safety.py              Content safety filter
  sources/               Config-driven multi-source system
    bluesky_timeline.py  Bluesky feed poller
    rss.py               Generic RSS/Atom poller
    feed_file.py         File watcher (stichomythia feed)
    stimuli_dir.py       Directory watcher (manual stimuli)
    multiplexer.py       Merges sources into single stream
    seeds.py             Accumulates seed context for composition

config/
  config.yaml            Sources, rate limits, self-generation modes
  oblique_strategies.md  88-card deck (Eno/Schmidt + custom) for structural variety
  prompts/               Soul, composition, reflection, revision, engage prompts
  stichomythia_feed.md   Seeds from bot-to-bot dialogue
  stimuli/               Manual stimuli folder (gitignored)

scripts/
  build_site.py          Static site generator (Jinja2 → GitHub Pages)
  templates/site/        Templates: index, entry, notebook, reflection, RSS feed
  parse_*.py             Source-specific corpus parsers
  chunk_corpus.py        Intermediate JSON → stanza-level JSONL
  ingest_to_chroma.py    JSONL → ChromaDB embeddings

tools/
  lucubrator-clipper/    Chrome extension + localhost drop server

deploy/
  lucubrator.service     systemd unit (Raspberry Pi)
  daily-review.sh        Cron wrapper for Opus editorial review
  rebuild-site.sh        Cron wrapper for site generation
```

## Corpus

69,900+ chunks from 425+ poets. Sources: EEBO-TCP, Project Gutenberg, OTA, ProQuest, Delphi Poetry Anthology, Delphi Poets Series, Blake Archive.

Embedded locally with ChromaDB's ONNX all-MiniLM-L6-v2.

## Pipeline

1. **Sources**: Bluesky timeline, RSS feeds, manual stimuli (browser clipper), file seeds
2. **Triage** (Haiku): should the bot engage with this stimulus?
3. **Retrieval**: semantic search with poet cooling (overused poets excluded)
4. **Composition** (Opus): short-form (300 char) or long-form (2-3 paragraphs)
5. **Self-generation**: contemplate (single passage), compare (two passages), engage_self (unprompted essay)
6. **Oblique Strategies**: random structural constraint per composition
7. **Daily review** (Opus): select 3-5 for publication + 5-10 for notebook, write reflection
8. **Editorial revision** (Opus): catch voice tics, sharpen prose on selected entries
9. **Site build**: render to static HTML, push to GitHub Pages

## Running

```bash
# Unified source runner (reads config/config.yaml for sources)
./venv/bin/python -m src.runner

# Build site and push to GitHub Pages
./venv/bin/python scripts/build_site.py

# Start the browser clipper drop server
python3 tools/lucubrator-clipper/drop-server.py --rsync
```

Requires `ANTHROPIC_API_KEY` in `.env`. Timeline mode also needs `BSKY_HANDLE` and `BSKY_PASSWORD`.

## Deployment

Runs on a Raspberry Pi 4 (4GB) with SSD and 8GB swap. systemd service auto-restarts. Daily review at 23:30 UTC, site rebuild at 23:45 UTC.

## Setup

```bash
python3.13 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Corpus data and ChromaDB embeddings are not in the repo. Run the pipeline scripts to rebuild from sources.
