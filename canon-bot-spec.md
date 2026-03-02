# Canon Bot — Architecture Spec

A Bluesky bot whose critical intelligence is shaped by the English poetry canon. It writes in clear contemporary prose but *thinks through* the poetry — surfacing specific passages when they illuminate whatever's being discussed, and framing them with sharp, concise commentary.

## Design Principles

- The bot does not pastiche or imitate poetic diction
- It writes crisp analytical prose; the poetry does the heavy lifting
- Editorial judgment (what to surface, when to stay silent) is the personality
- It should be more interesting to follow than to interact with directly

---

## Project Structure

```
canon-bot/
├── config/
│   ├── config.yaml          # API keys, thresholds, rate limits
│   └── prompts/
│       ├── system.md         # Core personality/system prompt
│       ├── triage.md         # "Should I engage?" prompt (Haiku)
│       └── reflect.md        # Periodic self-review prompt
├── corpus/
│   ├── raw/                  # Plain text source files, one per poet
│   ├── chunks/               # Processed stanza-level chunks (JSONL)
│   └── metadata_schema.json  # Schema for chunk metadata
├── src/
│   ├── main.py               # Entry point, event loop
│   ├── bluesky.py            # AT Protocol client (post, reply, listen)
│   ├── brain.py              # LLM orchestration (triage → generate → post)
│   ├── memory.py             # Vector DB interface + conversation state
│   ├── corpus_loader.py      # Chunking pipeline: raw → chunks → embeddings
│   └── cost_guard.py         # Rate limiting, daily spend caps, logging
├── data/
│   ├── chroma/               # ChromaDB persistent storage
│   ├── interactions.db       # SQLite log of all bot activity
│   └── reflections.json      # Output of periodic self-review
├── scripts/
│   ├── chunk_corpus.py       # One-off: process raw texts into chunks
│   ├── embed_corpus.py       # One-off: embed chunks into ChromaDB
│   └── dry_run.py            # Test mode: logs what bot would do, no API calls
├── requirements.txt
└── README.md
```

---

## Component Detail

### 1. Corpus Pipeline (`corpus_loader.py`, `scripts/`)

**Input:** Plain text files per poet in `corpus/raw/`, ideally from Project Gutenberg or similar. One file per poet or per major work.

**Chunking strategy:**
- Split by stanza or verse paragraph (not fixed token windows)
- Sonnets = 1 chunk each
- Longer forms (blank verse, ode): split at natural stanza/paragraph breaks
- Keep chunks roughly 50–300 tokens; allow longer for indivisible passages
- Each chunk gets metadata:

```json
{
  "poet": "Andrew Marvell",
  "work": "An Horatian Ode upon Cromwell's Return from Ireland",
  "date": 1650,
  "form": "ode",
  "period": "early_modern",
  "themes": ["politics", "power", "ambivalence", "war"],
  "chunk_text": "He nothing common did or mean / Upon that memorable scene...",
  "chunk_index": 3,
  "line_range": "33-40"
}
```

**Themes field:** Don't try to be exhaustive. A few high-level tags per chunk are enough for hybrid filtering. The semantic embeddings handle the nuance.

**Embedding:** Use an embedding model (e.g. `text-embedding-3-small` from OpenAI, or a local model like `all-MiniLM-L6-v2` via sentence-transformers if you want to avoid extra API costs). Embed the chunk text + poet + work title concatenated.

**Note on local embeddings:** Running `sentence-transformers` on the Pi 4 is feasible for the initial embedding pass (slow but one-off). For runtime query embedding, it adds ~1-2s latency per query — acceptable for a bot that doesn't need sub-second responses. This avoids per-query embedding API costs entirely.

### 2. ChromaDB (`memory.py`)

**Collections:**
- `canon`: The poetry corpus embeddings + metadata
- `interactions`: Embeddings of past bot posts + context, for avoiding repetition

**Retrieval flow:**
1. Embed the stimulus (the Bluesky post the bot is considering responding to)
2. Query `canon` collection: top 5-8 results by cosine similarity
3. Optionally filter by metadata (e.g. weight earlier periods, exclude recently used poets)
4. Query `interactions` collection to check the bot hasn't made this connection recently
5. Return ranked candidates to the brain

**Anti-repetition:** Track which poems/passages the bot has used in the last N days. Penalise or exclude them from retrieval results. The bot should range across the canon, not fixate.

### 3. Brain (`brain.py`)

Three-stage pipeline per potential interaction:

**Stage 1 — Triage (Haiku, cheap)**

Input: The Bluesky post text
Prompt: "Is this post something where a specific passage of English poetry would genuinely illuminate, complicate, or reframe what's being said? Not everything needs poetry. Say YES or NO and one sentence why."

This is the taste filter. Most posts get NO. The bot should be selective — posting 5-15 times a day, not 50.

**Stage 2 — Retrieval + Selection (no LLM, just vector search)**

If triage = YES, query ChromaDB as above. Return top candidates with metadata.

**Stage 3 — Composition (Sonnet)**

Input: Original post + top 3-5 retrieved passages with metadata
System prompt: See `config/prompts/system.md`

The system prompt should establish:
- Voice: precise, contemporary, occasionally wry. Not academic, not casual. Think of the tone of a very good TLS review.
- Approach: lead with an observation about what the post is getting at, then bring in the poetry. The lines should feel *necessary*, not decorative.
- Length: Bluesky posts are 300 chars. The bot can thread if needed but should prefer concision. If the passage + framing can't fit in 300 chars, it can do a short thread (2-3 posts max).
- What NOT to do: no "This reminds me of...", no "As [Poet] once wrote...", no explanations of the poem. Trust the reader. If the connection isn't self-evident with minimal framing, pick a different passage.

**Output format from LLM:**
```json
{
  "decision": "post" | "skip",
  "reason": "why skip, if skipping",
  "posts": [
    {
      "text": "The post text",
      "reply_to": "uri of post being replied to, or null for standalone"
    }
  ],
  "passage_used": {
    "poet": "...",
    "work": "...",
    "lines": "..."
  }
}
```

### 4. Bluesky Client (`bluesky.py`)

Use the `atproto` Python library.

**Engagement modes:**
- **Firehose monitoring:** Listen for posts matching keyword patterns or from followed accounts. This is the primary stimulus source.
- **Mentions:** Always respond to direct mentions (after triage).
- **Scheduled posts:** Optional — the bot could post a standalone passage + commentary once or twice a day without needing a stimulus, just surfacing something from the canon. Good for building a following.

**Firehose filtering keywords:** Keep a configurable list. Start broad (politics, death, love, war, nature, memory, grief, ambition, failure, England, sea, time, beauty, god) and refine based on what produces good results.

### 5. Cost Guard (`cost_guard.py`)

```yaml
# config.yaml
cost_guard:
  max_daily_api_spend_gbp: 3.00
  max_responses_per_hour: 5
  max_responses_per_day: 20
  cooldown_after_thread_minutes: 30
  triage_model: "claude-haiku-4-5-20251001"
  composition_model: "claude-sonnet-4-5-20250929"
  reflection_model: "claude-sonnet-4-5-20250929"
  reflection_frequency_hours: 24
```

Log every API call with model, token counts (input + output), and estimated cost to `interactions.db`. The bot should shut up if it hits the daily cap rather than failing noisily.

### 6. Reflection Loop

Once daily (or on-demand), run a reflection pass:
- Pull last 24h of bot posts from `interactions.db`
- Send to Sonnet with the reflect prompt: "Review these posts. Are any repetitive? Did any passages feel forced? Are you over-relying on any poet or period? What topics produced your best work? Suggest adjustments."
- Store output in `reflections.json`
- These reflections get prepended to the system prompt for the next day's composition calls, giving the bot a crude learning loop

### 7. Logging (`interactions.db`)

SQLite. Minimal schema:

```sql
CREATE TABLE interactions (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    stimulus_uri TEXT,
    stimulus_text TEXT,
    triage_result TEXT,          -- 'yes'/'no'
    triage_reason TEXT,
    passages_retrieved TEXT,     -- JSON array
    passage_selected TEXT,       -- JSON object
    response_text TEXT,
    response_uri TEXT,
    model_used TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    estimated_cost_gbp REAL
);

CREATE TABLE daily_stats (
    date TEXT PRIMARY KEY,
    total_responses INTEGER,
    total_cost_gbp REAL,
    reflection_notes TEXT
);
```

---

## Portability: Mac ↔ Pi 4

The project is a single folder. Everything is Python + filesystem.

**`requirements.txt`:**
```
atproto>=0.0.46
chromadb>=0.4.22
anthropic>=0.40.0
sentence-transformers>=2.2.2    # only if using local embeddings
pyyaml>=6.0
```

**Platform differences:**
- `sentence-transformers` model download is ~90MB; will be slower on Pi but only needed once
- ChromaDB data dir (`data/chroma/`) is portable — copy it from Mac to Pi and it works
- Python 3.11+ on both platforms
- On Pi: run via systemd service for persistence. On Mac: just run `python src/main.py`

**systemd service file (for Pi):**
```ini
[Unit]
Description=Canon Bot
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/canon-bot
ExecStart=/home/pi/canon-bot/venv/bin/python src/main.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

---

## Corpus Scoping — Editorial Guidance

This is a starting point. Adjust based on what retrieval actually surfaces.

**Complete works:** Shakespeare, Milton, Donne, Herbert, Marvell, Pope, Dryden, Blake, Wordsworth (including The Excursion), Coleridge, Keats, Shelley, Byron, Tennyson, Browning (both), Hopkins, Hardy, Yeats.

**Selected major works:** Spenser (FQ + selected shorter), Chaucer (CT + Troilus), Langland (Piers Plowman B-text), Sidney, Jonson, Herrick, Vaughan, Crashaw, Cowper (The Task + selected), Thomson (The Seasons), Gray, Collins, Smart (Jubilate Agno + Song to David), Goldsmith, Burns, Clare, Arnold, Swinburne, Christina Rossetti, Dante Gabriel Rossetti, Morris, Housman, Edward Thomas.

**Curated selections:** Skelton, Wyatt, Surrey, Drayton, Daniel, Denham, Waller, Rochester, Prior, Gay, Cowley, Traherne, Clough, Patmore, Meredith, Kipling, Dowson, Lionel Johnson, Davidson.

**The cut-off question:** This spec assumes a roughly pre-1920 canon. Extending to Eliot, Auden, Larkin etc. is a separate editorial decision with copyright implications for the texts themselves.

---

## Development Sequence

1. **Corpus first.** Gather texts, run chunking pipeline, embed. Test retrieval quality by hand — feed it topics and see what comes back. This is the foundation.
2. **Brain in isolation.** Test the triage → retrieve → compose pipeline locally against sample Bluesky posts (scraped or invented). Iterate the prompts. No posting.
3. **Dry run mode.** Connect to Bluesky firehose, run full pipeline, log everything, post nothing. Review logs daily for a week. Tune triage sensitivity and keyword filters.
4. **Go live cautiously.** Low rate limits. Monitor closely. Expand gradually.
5. **Reflection loop.** Add once the bot has a week of real posts to reflect on.
