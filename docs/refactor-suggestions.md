# Refactor Suggestions — Pre-Chat Mode

From audit verification session, 31 March 2026. Address these before adding chat mode or long-form essays.

## 1. Tool response extractor in brain.py

Seven functions (compose, reflect, daily_review, revise_entry, engage, contemplate, compare) all repeat the same pattern: loop `response.content`, find `tool_use` block, extract `block.input`, normalize `passage_used`, set defaults, attach `_usage`. Extract into a single helper like `_extract_tool_result(response, usage, defaults)`. Cuts ~100 lines and makes validation impossible to forget in new functions.

## 2. Split engine.py

Engine currently handles: rate limiting, passage enrichment, reflection context, oblique strategies, self-generation (3 modes), post-composition reflection, daily review orchestration, logging. `self_generate()` alone is 130 lines with three mode branches sharing most of their structure.

Suggested split:
- **engine.py** — rate limiting, cooldown, dedup, logging (the orchestrator)
- **self_gen.py** — self-generation with shared skeleton + per-mode composition
- **enrichment.py** — passage enrichment, reflection context building

This pays off directly for chat mode (new composition path) and essays (new self-gen mode).

## 3. Retriever singleton retry

Global `_client` / `_collection` with lazy init caches the result forever. If ChromaDB loads successfully but later becomes corrupt mid-session, the cached object serves a dead collection with no recovery path short of restart. Fix: on query failure, clear the cached `_collection` and retry once before returning empty.

## 4. Stimulus pipeline consolidation

Stimulus processing is split across three places:
- **runner.py** — handles stimuli_dir items as seeds, special-cases engage
- **engine.py** — `engage_stimulus()`, `self_generate()`, `process()` all touch stimuli differently
- **multiplexer.py** — dispatch logic

The path from file drop to composition is hard to follow. A dedicated stimulus pipeline (drop → classify → compose → log) would clarify the clipper path and make it easier to add new stimulus types (e.g. chat messages).
