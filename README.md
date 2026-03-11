# Lucubrator

A Bluesky bot whose critical intelligence is shaped by the English poetry canon (c. 1250–1900). It writes in clear contemporary prose but *thinks through* the poetry — surfacing specific passages when they illuminate whatever's being discussed.

Currently in **blog-only dry run**: polling a Bluesky timeline, running the full pipeline, publishing to a static site at [stwalsh.github.io/lucubrator](https://stwalsh.github.io/lucubrator). No posting to Bluesky yet.

## Design Principles

- The bot does not pastiche or imitate poetic diction
- It writes crisp analytical prose; the poetry does the heavy lifting
- Editorial judgment (what to surface, when to stay silent) is the personality
- It should be more interesting to follow than to interact with directly

## Architecture

```
src/
  brain.py           Three-stage pipeline: triage → retrieval → composition
  retriever.py       ChromaDB semantic search (single + multi-query)
  safety.py          Content safety filter (regex, 6 categories)
  engine.py          Orchestrator: rate limiting, anti-repetition, reflection loop
  store.py           SQLite interaction store + readings + passage notes + reflections
  web.py             Local test UI
  dashboard.py       Read-only review dashboard
  bluesky/
    client.py        AT Protocol: auth, post, reply, timeline
    firehose.py      Jetstream WebSocket consumer + keyword filter
    timeline.py      Timeline poller (cursor-based, configurable interval)
    runner.py        Main loop: firehose/timeline → engine → log

config/
  config.yaml        Paths, rate limits, keywords, chunking params
  prompts/
    triage.md        Triage prompt (Haiku)
    system.md        Composition rules (Sonnet)
    soul.md          Bot voice and self-concept
    reflect.md       Post-composition reflection prompt
    daily_reflect.md Daily digest reflection prompt

scripts/
  build_site.py      Static site generator (Jinja2 → GitHub Pages)
  templates/site/    Site templates and CSS
  chunk_corpus.py    Intermediate JSON → stanza-level JSONL
  ingest_to_chroma.py  JSONL → ChromaDB embeddings
  parse_*.py         Source-specific parsers (Gutenberg, EEBO, OTA, Delphi, etc.)

mcp_server/          FastMCP server: 7 tools + 2 resources
```

## Corpus

53,000+ chunks from 420+ poets. Sources: EEBO-TCP, Project Gutenberg, OTA, ProQuest, Delphi Poetry Anthology, Delphi Poets Series, Blake Archive.

Embedded locally with ChromaDB's ONNX all-MiniLM-L6-v2.

## Brain

1. **Triage** (Haiku): should the bot engage with this post?
2. **Safety filter**: regex scan of retrieved passages for harmful content
3. **Retrieval**: semantic search over the corpus, with anti-repetition
4. **Composition** (Sonnet): write the response, choose the mode, or skip
5. **Reflection**: per-passage notes + daily digest of patterns and preoccupations

## Running

```bash
# Blog-only mode: poll your timeline, process through brain, log to DB
./venv/bin/python -m src.bluesky.runner --timeline

# Build site and push to GitHub Pages
./venv/bin/python scripts/build_site.py

# Build site locally (no push)
./venv/bin/python scripts/build_site.py --no-push

# Firehose dry run (keyword-filtered)
./venv/bin/python -m src.bluesky.runner

# Test UI
uvicorn src.web:app --port 8080
```

Requires `ANTHROPIC_API_KEY` in `.env`. Timeline and live modes also need `BSKY_HANDLE` and `BSKY_PASSWORD`.

## Setup

```bash
python3.13 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Corpus data and ChromaDB embeddings are not in the repo. Run the pipeline scripts to rebuild from sources — see `config/config.yaml` for source configuration.
