# Stabilisation Audit — 31 March 2026

## Critical (fix now)

1. **Missing `import json` in build_site.py** — notebook rendering crashes on stringified passage_used
2. **No try/except on API calls in brain.py** — all 7 `client.messages.create()` calls unprotected. Any API error crashes the pipeline.
3. **No validation of tool call responses** — brain.py returns whatever Opus gives without checking required fields exist
4. **Empty self_notes stored without warning** — engine.py stores blank self_notes, breaking continuity
5. **Multiplexer infinite loop on source error** — exception → re-schedule → same exception → tight loop
6. **File deletion race in stimuli_dir** — stat() then read_text() with no exception handling between
7. **RSS seen_ids set ordering bug** — `set(list(seen_ids)[-500:])` doesn't preserve order, breaks dedup
8. **Config YAML parse error crashes bot** — only catches FileNotFoundError, not YAMLError
9. **ChromaDB unavailability unhandled** — retriever crashes if chroma is corrupt or missing

## High (fix soon)

10. **Daily review stores blank reflection on partial failure** — the March 30 bug
11. **Self-gen timeout doesn't reset timer** — hammers API on repeated timeouts
12. **Stale timeline cursor loops** — old cursor returns empty feeds indefinitely
13. **Drop server vision call no timeout** — blocks server if API is slow
14. **Passage enrichment matches wrong poem** — poet-only fallback ignores poem_title
15. **Dead code: brain.run()** — never called, duplicates engine logic
16. **Duplicate line in store.py:160-161** — copy-paste error
17. **DB indexes missing** — interactions table has no indexes, will slow on Pi

## Ongoing: Daily review tool response is the most unreliable data path

The `daily_review` tool schema specifies `selected_ids` as `[{id: int, tier: str, reason: str}]`, but Opus has returned:
- Flat list of strings (`["1318", "1320"]`) — caused March 31 crash
- Flat list of ints (`[1318, 1320]`)
- Dicts without tier field (backward compat fallback handles this)
- Empty/truncated self_notes (max_tokens fix helped but not 100%)
- Preoccupations as a single string instead of a list (caused character-by-character bullet rendering)
- Summary truncated to 0 chars on partial failure (March 30)

**Every field returned by the daily review tool must be defensively normalized.** The tool schema is a suggestion to Opus, not a contract. New failure modes will continue to appear. The normalization code in `engine.py:run_daily_reflection()` is the most important defensive code in the system.

## Medium (noted)

- Hardcoded values scattered (stimulus dedup cap, theme hours, fallback themes)
- No logging of which model used for which request
- Token tracking incomplete on partial failures
- Seed accumulator never clears stale seeds
- Cursor migration doesn't delete old file
- Multipart parser in drop server is naive
- build_site.py does JSON parsing that store.py should have already done
- reflections_by_date overwrites duplicates silently
- No timeout on git operations in site builder

## Low (noted)

- Inconsistent log levels
- Global mutable state in drop server
- Config backward compat code inline in runner
- No distinction between skip reasons
- Post reflection only on "post" decision (by design?)
