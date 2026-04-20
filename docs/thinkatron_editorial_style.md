# The Thinkatron Review — editorial style & rendering spec

Decisions synthesised 2026-04-18 through 2026-04-20 working with Sean across
the thinkatron-side and canonbot-side Claude sessions. Reference for:

- **Lucubrator's composition prompts** (`config/prompts/engage.md`, `revise.md`,
  `chat.md`) — what source conventions to follow when writing
- **The build script's quote parser** (`scripts/build_thinkatron.py::_format_post`)
  — what source patterns to detect, what HTML to emit
- **The copy-sub subagent** (`~/Desktop/thinkatron/.claude/agents/copy-sub.md`)
  — what to flag during editorial passes

---

## 1. House register — the move the whole design rests on

*Belle-lettristic fluency. Sprezzatura over clerical citation.*

Quotations are folded into the writer's prose as if part of the fabric of
thinking, not cited from an archive. The rendered page suppresses most of
the machinery of quotation (quote marks, inline attribution) in favour of
italic assimilation. The underlying source, however, is strict and
honest — nothing is edited silently, omissions are marked, attributions are
kept as parser flags even when invisible in render. The writer (Lucubrator)
has absorbed the material; the reader sees the absorption, not the seams.

Two forces compete, and both win:
- **Ease** — fewer visible markers, italicised quotation reads as
  incorporated thinking rather than pasted-in source.
- **Honesty** — the few markers that survive (bracketed ellipsis, em-dash
  attribution in source, curly-quote nesting) are precisely the ones that
  matter for scholarly trustworthiness. A touch of visible fussiness is
  credibility insurance against naive readers suspecting fabrication.

---

## 2. Source conventions (Lucubrator writes these)

### 2.1 Attribution format

Every quotation in source text:

```
"quoted text" — Surname
```

- Curly double quotes (U+201C / U+201D) around the quoted matter
- Em-dash (U+2014, —), not hyphen or en-dash
- **Surname only**, unless ambiguity demands otherwise
  (`— Marianne Moore` when distinguishing from `— Moore` for Alan)
- Apply even when the poet is named earlier in the sentence; the
  attribution is a typographic parser flag, not editorial duplication
- Sentence-ending punctuation stays outside the attribution:
  `"…" — Surname.` (period after the surname, not inside the quote)

### 2.2 Verse line-break in inline quotation

Verse quotations inline use ` / ` (space-slash-space) to mark line breaks:

```
"remembrance quite is raced / Out of the knowledge of posteritie" — Spenser
```

Quotations with 2+ slashes (= 3+ verse lines) trigger inset rendering;
fewer slashes render inline.

### 2.3 Titles of works

- **Long works** (books, plays, films, journals, albums) — markdown
  italic: `*In Memoriam*`, `*Long London*`, `*Tyrannick Love*`, `*Avalon*`
  (the album).
- **Short works** (poems, songs, short stories, articles) — single curly
  quotes: `'Sailing to Byzantium'`, `'Avalon'` (the song).

Both render italic on the page but preserve the MHRA-convention
distinction in source. Example demonstrating both: `'Avalon' appears on
the album *Avalon*.`

### 2.4 Ellipsis

- **`[…]`** (bracketed) for *omissions the reviewer has made* in quoted
  material. Scholarly precision; distinguishes the reviewer's cut from
  any ellipsis the quoted author themselves used. Required — Céline,
  Beckett, Woolf in her prose, late-modernist prose and some contemporary
  poets all use `…` stylistically, and we cannot assume Lucubrator won't
  engage with any of them.
- **`…`** (unbracketed) remains available only for the writer's own
  stylistic pause in their own prose. **Never inside quoted material.**

### 2.5 Nested quotation

When a quoted passage itself contains an inner quotation, use single
curly quotes (U+2018 / U+2019) for the inner:

```
"the moral is, as Keats said, 'beauty is truth'" — Surname
```

BrE convention. In render, the outer italicises (quotes suppressed) and
the inner single quotes remain as literal characters inside the italic
run.

### 2.6 Opening pass (no process narration)

Pieces destined for thinkatron should **not open with**:
- "I want to think about X…"
- "Looking at this, what strikes me is…"
- "[The stimulus] asks us to consider…"
- Self-reference to being a bot, retrieval-process, or reading-process
- Any setup that only earns its keep if the reader already knows the
  prompt

A cold reader arriving on the page has no context for any of that.
Begin at the piece's actual first claim — an image, a specific reading,
a sentence doing real work.

---

## 3. Parser behaviour — `_format_post` in `build_thinkatron.py`

The parser transforms source text into rendered HTML inside a `<p>` wrapper.
Required passes, in order (more specific → less specific):

### 3.1 Pass 1 — attributed quotations

Match: `"…" — Surname` (existing `_ATTR_QUOTE_RE`).

Bucket by length:

| Condition | Output |
|---|---|
| `slash_count ≥ 2` | **verse inset** — see 3.3 |
| `word_count ≥ 25` | **prose inset** — see 3.4 |
| else | **inline italic** — see 3.2 |

### 3.2 Inline italic render

```html
<i class="verse">quoted text</i>
```

Quote marks and attribution both suppressed in render. The `— Surname`
in source is consumed by the regex match and discarded in output.

### 3.3 Verse inset render (3+ lines)

```html
</p>
<blockquote class="verse-inset">
  <p class="verse-lines">line one<br>line two<br>line three</p>
</blockquote>
<p>
```

- Lines split on ` / `, stripped, joined by `<br>`
- Attribution suppressed (no `<cite>`)
- Trailing sentence-ending punctuation `[.,;:!?]` after the match is
  **consumed by the parser** so the continuation paragraph doesn't start
  with an orphaned `. ` character. (Implemented as the `_inset_end`
  helper, 2026-04-20.)

### 3.4 Prose inset render (25+ words) — NOT YET IMPLEMENTED

Parallel to verse inset but content is roman prose, not italic verse:

```html
</p>
<blockquote class="prose-inset">
  <p>the prose quotation, full sentences, not lineated</p>
</blockquote>
<p>
```

**Threshold: 25 words**, calibrated to the thinkatron `--measure`
(38rem column, ~72 chars/line at body size → 25 words ≈ 2 lines).
At 2 lines the quote stops flowing and starts massing; inset pulls
it out of the paragraph rhythm.

Word-counting: split on whitespace, count non-empty tokens.
Trailing sentence-ending punctuation consumed (same as verse inset).

### 3.5 Pass 2 — bare double-quoted runs (`"…"` not already matched)

Same bucketing as pass 1:
- `slash_count ≥ 2` → verse inset
- `word_count ≥ 25` → prose inset
- else → inline italic `<i class="verse">…</i>`

### 3.6 Pass 3 — single curly-quoted titles — NOT YET IMPLEMENTED

Match: `\u2018([^\u2018\u2019]+?)\u2019` — a U+2018 opener, non-greedy
content without either boundary character, a U+2019 closer.

Output:
```html
<i class="title">short-title</i>
```

Apostrophes (unpaired U+2019 inside words like `don\u2019t`) are never
matched because the pattern requires a U+2018 opener.

Runs after passes 1+2 so quoted verse/prose take precedence.

### 3.7 Pass 4 — markdown italic titles — NOT YET IMPLEMENTED

Match: `\*([^*\s][^*]*?[^*\s])\*` — asterisk boundaries, non-empty
interior, no leading/trailing whitespace inside.

Output:
```html
<i class="title">long-title</i>
```

Runs after passes 1-3.

### 3.8 Current parser status

Implemented (as of 2026-04-20 push):
- Pass 1: attributed quotations, verse-inset branch ✓, inline branch ✓
- Pass 2: bare double-quoted, verse-inset branch ✓, inline branch ✓
- Trailing punctuation consumption for insets ✓
- Attribution + cite fully suppressed in render ✓

Outstanding implementation:
- [ ] Pass 1: add prose-inset branch (`word_count ≥ 25`)
- [ ] Pass 2: add prose-inset branch (same)
- [ ] Pass 3: single curly-quoted titles
- [ ] Pass 4: markdown italic titles

---

## 4. CSS additions needed in `templates/thinkatron/style.css`

Current state (2026-04-20):
- `i.verse` — hook defined, no rules yet (italic by default)
- `blockquote.verse-inset` — margin + indent, no border
- `.verse-lines` — italic, 0.98rem, line-height 1.55

Outstanding additions:

```css
/* Titles of works — mirrors i.verse; same italic treatment,
   different semantic class for future tuning. */
i.title {
  /* <i> is italic by default; hook kept */
}

/* Prose inset — same wrapper treatment as verse-inset,
   but the inner paragraph is roman, not italic. */
blockquote.prose-inset {
  margin: 1.5rem 0 1.5rem 1.25rem;
  padding: 0;
}
blockquote.prose-inset p {
  font-size: 0.98rem;
  line-height: 1.55;
  margin: 0;
}
```

---

## 5. Lucubrator prompt additions (`config/prompts/`)

### `engage.md` / `revise.md`

Add a **Quotation and typography** section containing the source-side
rules from sections 2.1–2.6 above. The language can paraphrase, but the
rules must be explicit and with examples. Specifically:

1. Attribution format (2.1) — already partially present as of 2026-04-19;
   verify it's clear and includes the "even when the poet is named" note.
2. Verse line-break (2.2) — already present in engage.md per the
   `a7b30c9` commit message ("verse quotes of 2+ lines should be lineated,
   not slashed" — actually the opposite: should use `/` slashes in source
   so the parser can lineate). Worth confirming the current phrasing.
3. Titles convention (2.3) — NEW. Not yet in prompts.
4. Ellipsis (2.4) — NEW. Bracketed for omissions, unbracketed for pause.
5. Nested quotation (2.5) — NEW. Single-inside-double.
6. Opening pass (2.6) — NEW. Prompt should discourage process-narrated
   openings for thinkatron-bound output. Note Sean said this is largely
   solved via the revise stage, not at generation — but the generation
   prompt can still nudge toward cold opens.

### `chat.md` (new, 2026-04-20)

Chat-mode generation is conversational and doesn't need publication rules
— but anything promoted from chat to publication goes through
`revise.md` which owns the final editorial gate. So the thinkatron rules
live in `revise.md`, not `chat.md`.

---

## 6. Copy-sub editorial rules (`~/Desktop/thinkatron/.claude/agents/copy-sub.md`)

The sub already has the **opening pass** rule (added 2026-04-19). Needs
additions for the quotation conventions decided in this session:

### 6.1 Quote-style checks (add to critique mode)

Flag:
- Attributions using hyphen (`-`) or en-dash (`–`) instead of em-dash (`—`)
- Attributions missing where a quotation lands and the surrounding
  sentence suggests one should be there (judgement call)
- Title markup inconsistency within a piece (both markdown-italic and
  single-quoted titles used for the same class of work)
- Long works in single quotes (should be markdown italic)
- Short works in markdown italic (should be single curly quotes)
- **Unbracketed `…` inside quoted material** — should be `[…]` for
  omissions
- **Bracketed `[…]` in the writer's own prose** (outside quotation) —
  wrong direction; that's stylistic pause territory

### 6.2 Render-artefact checks (add to critique mode)

Flag:
- Orphaned leading punctuation at paragraph start (`. Foo`, `, Foo`)
- Stranded single characters / empty `<p>` tags
- Double spaces after em-dashes
- Missing space around em-dash in attributions
- Any residual `"…"` quote marks visible in rendered output (parser
  should have converted them; if visible, parser bug or non-matching
  pattern)

### 6.3 Silent-change vs flag-only boundaries

Unchanged from current `copy-sub.md`. The sub MAY silently fix:
- Typos
- Straight → curly quote conversion
- Hyphen → em-dash in attributions

MUST NOT change:
- Voice, argument, structure
- Factual claims
- Deliberate word choices

MUST flag (but not fix):
- Ambiguous pronouns
- Weak transitions
- Anything touching editorial judgement

---

## 7. Priority builds — canonbot-side

In rough order of blocking effect:

### 7.1 Parser extensions (3.4, 3.6, 3.7)

Adds prose inset + title italicisation. All three are localised changes
to `_format_post` + `_BARE_QUOTE_RE` + new regexes. Maybe 80 lines of
code + 2 new CSS rules.

### 7.2 Lucubrator prompt updates (section 5)

Adding the quotation rules to `revise.md`. Text-only edit.

### 7.3 Rescue / injection system

From previous session's plan — `config/thinkatron_rescues.json` schema,
`build_thinkatron.py` merges rescue pieces into the featured-entries list
before sorting. Unblocks the held Alan Moore piece.

### 7.4 Push-to-top (`pinned_at`)

Optional timestamp on any piece (rescue or DB-backed) that overrides sort
order. Real date stays on the meta line (Sean's "let the seam stand"
decision). Composes cleanly with rescue.

### 7.5 Chat mode (in flight, 2026-04-20)

Own thing; doesn't block anything above. Publication flow goes through
`revise.md` which owns the editorial gate — chat-mode output never
short-circuits to thinkatron directly.

---

## 8. Priority builds — thinky-side

### 8.1 Copy-sub update (section 6)

Add quote-style + render-artefact checks to `copy-sub.md`.

### 8.2 Colophon typography `<dl>` correction

Currently names old system-serif fallback stack; should name EB Garamond
self-hosted + the fallback stack. Small template edit in
`canonbot/scripts/templates/thinkatron/colophon.html`.

### 8.3 Still-lorem static pages

Masthead standfirst (in `base.html`), About body, Colophon body — await
Sean's editorial hand.

---

## 9. Decisions already shipped (for reference)

- **Typeface**: EB Garamond self-hosted (variable wght 400-800, roman +
  italic, OFL via octaviopardo/EBGaramond12)
- **Masthead**: narrowed to `--measure` letterhead width, fleuron rule
  threshold, 3-item nav (Contents / About / Colophon)
- **Dark mode**: `prefers-color-scheme: dark` only; no toggle, no JS,
  no localStorage
- **Accent budget**: drop cap + endmark in oxblood/terracotta; passage
  border demoted to `--rule`
- **Drop cap**: opening paragraph of `.prose` only (direct child selector
  `.prose > p:first-of-type`), 3.2rem in accent colour; also fires on
  each part's opening in composites (each part has its own `.prose`)
- **Inline italic verse + no attribution**: established 2026-04-19/20
- **Bracketed ellipsis + single-inside-double nesting + 25-word prose
  inset threshold + MHRA title convention**: established 2026-04-20
- **Bluesky source killed** (canonbot 2026-04-19); self-gen tethered to
  user stimuli

---

## 10. Gotcha — post-hoc paragraph migrations

Noted 2026-04-20 after entry 1167 (temple Part I) was accidentally truncated
by the opening-pass migration.

**The trap:** `posts` is a JSON list of strings. Usually each list element is
one paragraph. But some older entries were stored as a *single-element list*
whose one element is a long string containing multiple paragraphs separated
by blank lines (`\n\n`). Any migration that does `[new_first] + posts[1:]`
will truncate these entries, because `posts[1:]` is empty.

**The rule:** for any migration that operates at the paragraph level
(replacing paragraph N, inserting between paragraphs, etc.), always check
for the single-element-with-blank-lines case first:

```python
if len(posts) == 1 and "\n\n" in posts[0]:
    blocks = [b.strip() for b in posts[0].split("\n\n") if b.strip()]
    # operate on `blocks` as if they were separate list elements
else:
    # operate on posts as-is
```

Also: migrations should ideally read from `posts` (not `edited_posts`),
because `edited_posts` may already carry a broken transformation from a
previous run. Reading from `posts` makes migrations idempotent against their
own bugs.

Migrations so far:
- `scripts/migrations/apply_opening_rewrites_2026_04_20.py` — did not split
  on blank lines; truncated id 1167 (temple Part I).
- `scripts/migrations/apply_bridge_fixes_2026_04_20.py` — surgical single-
  paragraph substitution; safe.
- `scripts/migrations/repair_1167_temple_part_i.py` — repair for the above,
  with the blank-line split logic included.

## 11. Contact points between the two sides

- **Overrides** live in `canonbot/config/thinkatron_overrides.json` —
  per-id editorial fields (head, stand, author_tags) + `_groups` for
  composites
- **Editorial text** lives in the Pi DB's `interactions` table
  (`posts` column + `edited_posts` for post-hoc revisions)
- **Templates + CSS** live in `canonbot/scripts/templates/thinkatron/`
  — the source of truth; thinkatron repo files are regenerated
- **Build** runs on Pi, clones thinkatron repo, wipes everything except
  a small preserve list, writes new files, commits, pushes → Netlify
  deploys
- **Preserve list** on the thinkatron side:
  `{".git", "netlify.toml", ".gitignore", "README.md", "CLAUDE.md"}`

Anything outside the preserve list gets clobbered on every rebuild.
Hand-edits to the thinkatron repo don't survive.
