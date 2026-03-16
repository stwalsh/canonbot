# Canon Bot — Status Report (5 March 2026)

## What This Is

A Bluesky bot whose critical intelligence is shaped by the English poetry canon (roughly 1250–1900). It writes in clear contemporary prose but *thinks through* the poetry — surfacing specific passages when they illuminate whatever's being discussed.

The core operation: identify the point where a living person's sentence and a dead person's line are about the same fracture, appetite, or failure. Articulate that collision. Sometimes the poem says it better. Sometimes you say something the poem can't.

## Architecture

```
Stimulus (Bluesky firehose, filtered by keyword + engagement)
    ↓
TRIAGE (Haiku 4.5) — should we engage?
    ↓ engage=true + search_queries + the_problem
SAFETY FILTER — regex scan, 6 categories, 200-char proximity
    ↓
RETRIEVAL — vector search across 47.5k chunks (ChromaDB, ONNX MiniLM-L6-v2)
    ↓ top passages, safety-filtered, anti-repetition (48h window)
COMPOSITION (Sonnet 4.5) — choose mode or skip
    ↓
ENGINE — rate limiting, logging to SQLite + log file
    ↓
Output (QT on own timeline, or blog post)
```

### Platform-agnostic core

The brain, engine, store, safety filter, and retriever are decoupled from any specific platform. Bluesky is one adapter; the blog will be another; the multi-agent dialogue site (future) would be a third.

```
src/
  brain.py           Three-stage pipeline: triage → retrieval → composition
  retriever.py       ChromaDB semantic search (single + multi-query)
  safety.py          Content safety filter (regex, 6 categories)
  engine.py          Orchestrator: rate limiting, anti-repetition, logging
  store.py           SQLite interaction store (data/interactions.db)
  dashboard.py       Read-only review dashboard (localhost:8081)
  web.py             Local test UI
  bluesky/
    client.py        AT Protocol: auth, post, reply, thread
    firehose.py      Jetstream WebSocket consumer + keyword filter
    runner.py        Main loop: firehose → engine → log (dry run) or post (live)

config/
  config.yaml        Paths, rate limits, keywords, chunking params
  prompts/
    triage.md        Triage prompt
    system.md        Composition rules
    soul.md          Bot voice and self-concept

mcp_server/          FastMCP server: 7 tools + 2 resources
```

## What's Built and Working

### Pipeline: end-to-end ✓

Dry run tested successfully against the live Bluesky firehose. The full chain works: Jetstream WebSocket → keyword filter → triage → retrieval → safety filter → composition → logging. Multiple runs totalling ~50 interactions, no crashes after hardening.

### Corpus: 47,567 chunks, 430+ poets ✓

| Source | Chunks | Notes |
|--------|--------|-------|
| EEBO-TCP | 29,272 | 26 target poets, 1475–1700 |
| Gutenberg | 12,000+ | OBEV, Lucasta, Don Juan, Browning, Keats, Tennyson, Wordsworth, Coleridge, Hopkins, Hardy, Housman, Kipling |
| Delphi | 5,789 | Keats, Tennyson, Shelley, Browning |
| Pope couplets | 2,322 | From popebot |
| ProQuest | 2,036 | Shelley (Hutchinson 1904) |
| Blake (Erdman) | 902 | Blake Archive API |
| Oxford Text Archive | 423 | Prelude (1850), Swinburne |

### Brain pipeline ✓

- **Triage** (Haiku 4.5): good discrimination. Engages on thematic posts, skips noise, rage content, RIPs, logistics. ~25-30% engagement rate on keyword-filtered posts.
- **Safety filter**: 6-category regex, 200-char proximity. 0.21% of corpus flagged. Belt-and-braces with composition prompt.
- **Retrieval**: multi-query semantic search with anti-repetition (48h window of used chunk_ids).
- **Composition** (Sonnet 4.5): produces quote_only, thought_quote, or thought_only. Tightened to reject surface-vocabulary matches — collisions must be at the level of subject, not diction.
- **Token tracking**: full input/output token counts per interaction.

### Engine ✓

- Rate limiting: configurable hourly/daily caps + post-to-post cooldown
- Anti-repetition: recently used passages excluded from retrieval
- All interactions logged to SQLite with full metadata
- Human-readable log file at `data/logs/canonbot.log`

### Bluesky adapter ✓

- Jetstream WebSocket consumer with keyword filter and English language filter
- AT Protocol client: login, post, reply, thread
- Dry-run mode (default): full pipeline, logs everything, posts nothing
- Live mode: `--live` flag

### Review tools ✓

- **Dashboard** (`src/dashboard.py`): web UI at localhost:8081. Daily stats (triaged/engaged/posted/tokens), reverse-chronological interaction cards with triage reasoning, compositions, passage attribution.
- **MCP server**: 7 tools for interactive corpus exploration.

## Dry Run Observations

### What works well

- **Triage reasoning** is genuinely good criticism. It identifies the deeper problem in a post with real interpretive sensitivity.
- **Best compositions** are striking: Pope on data-mining/surveillance, Browning on language-as-lying, Shakespeare on appetite preceding justification, Vaughan on performative grief.
- **Attribution style** refined to just `— Surname` at end of quote. Cleaner, more conversational.
- **Safety filter + triage** together keep the bot from engaging with RIPs, rage content, personal mourning, or posts where poetry would be condescending.

### What needs work

- **Keyword volume**: words like "time", "god", "power" match constantly. Multiple hits per second. Need engagement-threshold filtering or narrower keywords.
- **Forced matches**: occasionally composition picks a passage that shares vocabulary but not subject matter with the stimulus. Prompt tightened but still imperfect.
- **thought_quote bias**: most compositions choose this mode. May need further prompt tuning or mode rethinking (see Interaction Model below).

## Interaction Model (Evolving)

The current reply-bot model has problems:

1. **Anti-AI sentiment on Bluesky** is intense. An AI bot jumping into replies or QTing will provoke hostility regardless of quality.
2. **thought_quote threading** (observation + quote as 2-post reply) is intrusive. The bot's commentary, however good, is unsolicited.
3. **The best outputs read more like criticism than social media posts.** They want to breathe.

### Emerging model

- **Bluesky timeline**: QTs only, quote_only mode. The passage speaks for itself. No unsolicited commentary in anyone's mentions.
- **Blog** (linked from bio): where the bot's voice lives. Each entry is a triptych: the original post (blockquoted), the bot's thought (a few sentences, with emphasis), and the passage (properly formatted verse). Below the fold: triage reasoning, search queries — the working-out.
- **Engagement threshold**: only engage with posts that have reached some level of traction (e.g. 10+ quotes). Reduces noise, avoids jumping on throwaway thoughts.
- **Mentions** (future iteration): users can @-invoke the bot under a post. The bot reads the parent post and responds. Summoned, not uninvited.

### Blog architecture (next to build)

Static site generated from the SQLite interaction store. GitHub Pages, separate public repo. Build script reads DB, renders HTML with Jinja, pushes.

## Remaining Corpus Gaps

| Poet | Status |
|------|--------|
| W.B. Yeats | Not sourced. The big gap. ProQuest or LLDS best bet. |
| Christina Rossetti | Gutenberg available. Goblin Market + devotional lyrics. |
| Elizabeth Barrett Browning | Aurora Leigh + Sonnets from the Portuguese. Gutenberg. |
| Queen Mab (Shelley) | ProQuest throttling. User will find. |

## Dev Sequence

1. ~~Corpus — gather, chunk, embed~~ ✓
2. ~~Brain — triage, retrieval, composition~~ ✓
3. ~~Safety filter~~ ✓
4. ~~Fill corpus gaps~~ ✓ (Hopkins, Coleridge, Hardy, Housman, Dickinson, Kipling done)
5. ~~Dry run — firehose connected, logs everything, posts nothing~~ ✓
6. **Blog** — static site, generated from interactions DB ← HERE
7. **Refine interaction model** — engagement threshold, QT-only on Bluesky
8. **Soft launch** — obscure Bluesky account
9. **Add reflection loop**
10. **Mentions handling**

## Open Questions

### Engagement threshold mechanics
Jetstream gives raw posts, not engagement counts. Need to either poll the API for metrics on keyword matches, or watch quotes/reposts feeds instead. Different plumbing.

### Ring and the Book
12 books, ~21,000 lines of Browning dramatic monologues around a murder trial. On the fence. Covers unique ground (competing testimonies, forensic rhetoric) but risks Browning saturation.

### Multi-agent dialogue site
Parked until bot is live. See `ideas.md` for full design (panel of critic-voices: quasi-Kermode, quasi-Nuttall, quasi-Empson, etc.).

### Reflection loop
Post-composition self-review. Needs the bot to be live and producing real output before it's useful.

## Technical

- Python 3.13, venv at `./venv/`
- ChromaDB, Anthropic SDK, atproto, websockets
- Embeddings: ChromaDB ONNX all-MiniLM-L6-v2 (384-dim, local, free)
- Models: Haiku 4.5 (triage), Sonnet 4.5 (composition)
- GitHub: `stwalsh/canonbot` (private)
