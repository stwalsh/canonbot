# Audit Verification — 31 March 2026

Independent verification of fixes from `docs/audit-31-march-2026.md`, plus fixes for remaining gaps.

## Critical Items

| # | Issue | Pre-verification | Post-verification |
|---|-------|-----------------|-------------------|
| 1 | Missing `import json` in build_site.py | **FIXED** | OK |
| 2 | No try/except on API calls in brain.py | **FIXED** | OK |
| 3 | No validation of tool call responses | NOT FIXED | **FIXED** (this session) |
| 4 | Empty self_notes stored without warning | **FIXED** | OK |
| 5 | Multiplexer infinite loop on source error | **FIXED** | OK |
| 6 | File deletion race in stimuli_dir | **FIXED** | OK |
| 7 | RSS seen_ids set ordering bug | NOT FIXED | **FIXED** (this session) |
| 8 | Config YAML parse error crashes bot | **FIXED** | OK |
| 9 | ChromaDB unavailability unhandled | PARTIALLY FIXED | **FIXED** (this session) |

## High Items

| # | Issue | Pre-verification | Post-verification |
|---|-------|-----------------|-------------------|
| 10 | Daily review stores blank reflection | **FIXED** | OK |
| 11 | Self-gen timeout doesn't reset timer | **FIXED** | OK (runner.py:219 has 180s timeout; lines 236+239 reset timer) |
| 12 | Stale timeline cursor loops | **FIXED** | OK |
| 13 | Drop server vision call no timeout | **FIXED** | OK |
| 14 | Passage enrichment matches wrong poem | **FIXED** | OK |
| 15 | Dead code: brain.run() | **FIXED** | OK |
| 16 | Duplicate line in store.py:160-161 | **FIXED** | OK |
| 17 | DB indexes missing | **FIXED** | OK |

## Medium Items (unchanged — noted for future)

| Issue | Status |
|-------|--------|
| Hardcoded values (stimulus dedup cap, theme hours) | Not fixed |
| No logging of which model used per request | Not fixed |
| Token tracking incomplete on partial failures | **Fixed** |
| Seed accumulator never clears stale seeds | Not fixed |
| Cursor migration doesn't delete old file | **Fixed** |
| build_site.py re-parses JSON from store | Partially addressed |
| reflections_by_date overwrites duplicates silently | Not fixed |
| No timeout on git operations in site builder | Not fixed |

---

## Fixes applied this session

### Fix 1: ChromaDB crash propagation (item 9)

**File:** `src/brain.py` — `retrieve()` function

`retriever._get_collection()` logged errors but re-raised, crashing the bot on any ChromaDB issue. Wrapped `retriever.search_multi()` in try/except in `brain.retrieve()`. On failure, logs the error and returns `[]`. All callers already handle empty passage lists gracefully.

### Fix 2: Tool call response validation (item 3)

**File:** `src/brain.py` — 7 functions

Added `.setdefault()` calls for all required fields at every tool response extraction point:

- **`compose()`**: `decision`, `posts`, `mode` + `passage_used` dict fields (`chunk_id`, `poet`, `poem_title`)
- **`reflect()`**: `collision_note`, `themes`, `updated_note`
- **`daily_review()`**: `selected_ids`, `summary`, `preoccupations`, `recommendations`, `self_notes`
- **`revise_entry()`**: `revised_posts`, `changes_made`
- **`engage()`**: `decision`, `posts`, `paragraphs` + `passage_used` dict fields
- **`contemplate()`**: `decision`, `mode` + `passage_used` dict fields
- **`compare()`**: `decision`, `mode` + `passage_used` dict fields

If Opus omits a field, it gets a safe default instead of causing a `KeyError` downstream.

### Fix 3: RSS dedup ordering (item 7)

**File:** `src/sources/rss.py` — cursor trimming logic

Previous code: `set(list(seen_ids)[:500])` — `list()` on a set has arbitrary order, so recent IDs could be discarded and old entries reprocessed.

New code: builds an ordered list with current-cycle IDs first, then older IDs, and trims from the old end. Recent IDs are always preserved.

### Not a gap: Self-gen timeout (item 11)

Initial review missed that `runner.py:212–219` already wraps `self_generate()` in `asyncio.wait_for(..., timeout=180)`, and both the `TimeoutError` handler (line 236) and the generic `Exception` handler (line 239) reset `last_composition_time` to prevent hammering. This was already fixed.

---

## Final status

**All 17 critical/high items are now resolved.** Medium items remain as noted — none are crash risks.
