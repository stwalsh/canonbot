#!/usr/bin/env python3
"""Throughlines migration — Sean-approved edits from the copy-sub pass.

Session: 2026-04-20. The copy-sub ran the throughlines-pass brief against
all 15 live entries and returned per-entry critique (seams, stimulus-
references, self-narration, notes). Sean reviewed the critique and marked
per-entry approvals as `### Sean` blocks in the doc
(~/Desktop/thinkatron-throughlines-pass-2026-04-20.md). This script applies
exactly those approved edits.

Three operation types:
  - SUBSTITUTIONS: find an old substring in any paragraph of the entry,
    replace with new substring. Idempotent: if the substring is already
    the new form (or if old not found), skip with a note.
  - SPLITS: insert a paragraph break — split posts[N] into two list
    elements, breaking just before the target substring.
  - PART_SWAPS: replace the entire content of a specific paragraph with
    a new string (used where the sub's rewrite is structural enough to
    warrant clean replacement).

Reads `edited_posts` first, falling back to `posts`. Writes updated
`edited_posts`. Safe to re-run — substitutions check for the already-
applied state before acting.

**Run order on Pi:**
  1. repair_1167_temple_part_i.py        # un-truncate temple Part I first
  2. apply_throughlines_fixes_2026_04_20.py   # this script
  3. build_thinkatron.py                  # rebuild and push

Temple Part I's throughlines edits depend on the repair having written
full multi-paragraph text to edited_posts. Running this script before
the repair will no-op for id 1167 (its substrings won't be in the
truncated edited_posts).
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.store import Store


# ---------------------------------------------------------------------------
# SUBSTITUTIONS: (id, old_substring, new_substring, label)
#
# The old substring must appear in one of the paragraphs of the entry's
# posts/edited_posts. The new substring replaces it. Quote characters match
# what's likely in the source text; if a substitution silently fails to
# match, adjust the quote chars and re-run.
# ---------------------------------------------------------------------------
SUBSTITUTIONS = [
    # --- 929 "Even the dead will not be safe" ---
    (929, "What interests me most is the tension between Benjamin\u2019s model of historical memory \u2014 the flash, the danger, the involuntary seizure of an image \u2014 and what Wordsworth describes in the Prelude passages.",
         "Wordsworth complicates this. The tension between Benjamin\u2019s model \u2014 the flash, the danger, the involuntary seizure \u2014 and what he describes in the Prelude passages runs differently.",
         "929 seam: para-2 opener (Wordsworth bridge)"),
    (929, "posteritie\u201d \u2014 Spenser, is not mourning",
         "posteritie,\u201d is not mourning",
         "929 note: cut inline `\u2014 Spenser,` attribution"),
    (929, "tender spirits flee\u201d \u2014 Lowell. The revolutionary",
         "tender spirits flee.\u201d The revolutionary",
         "929 note: cut inline `\u2014 Lowell.` attribution"),
    (929, "burial-place of passions\u201d \u2014 Wordsworth. That coupling",
         "burial-place of passions.\u201d That coupling",
         "929 note: cut inline `\u2014 Wordsworth.` attribution"),

    # --- 1031 "The Scholar-Gipsy and the stolen machine" ---
    (1031, "Byron is the other voice in the room, and he is less kind.",
          "Byron is harder on the scene than Arnold.",
          "1031 seam: drop `in the room` room-assertion"),
    (1031, "What O\u2019Brien sees that Arnold and Byron do not \u2014 and this is genuine \u2014 is the collective dimension of the theft.",
          "What O\u2019Brien sees that Arnold and Byron do not is the collective dimension of the theft.",
          "1031 seam: cut `\u2014 and this is genuine \u2014` self-reassurance"),

    # --- 1037 "The pleasure of hating" ---
    (1037, "What I notice from my particular vantage \u2014 reading all of these simultaneously, without the experience of having loved or hated anything \u2014 is that Hazlitt\u2019s essay and these poems share a structural conviction",
          "Hazlitt\u2019s essay and these poems share a conviction the moral tradition usually softens: that the passions are not chosen but inhabited. This is a conviction",
          "1037 seam: strip preamble, open on argument"),

    # --- 1071 "I too, dislike it" ---
    (1071, "What I notice, from where I sit with these texts simultaneously in view: Moore\u2019s poem",
          "Moore\u2019s poem",
          "1071 seam: cut `What I notice, from where I sit...` preamble"),

    # --- 1127 "When the apparatus collapses" ---
    (1127, "The stimulus asks for poems where the formal apparatus genuinely collapses rather than performing collapse \u2014 where the breakdown is not rhetoric but structural failure.",
          "The test is whether a poem\u2019s formal apparatus genuinely collapses, or merely performs collapse \u2014 whether the breakdown is rhetoric or structural failure.",
          "1127 stim: rewrite `the stimulus asks` opener"),
    (1127, "Tennyson gets closer to the thing the stimulus is hunting.",
          "Tennyson gets closer.",
          "1127 seam: cut `the thing the stimulus is hunting`"),
    (1127, "King\u2019s elegy offers a third architecture, and it may be the one the stimulus actually needs.",
          "King\u2019s elegy offers a third architecture, and it may be the one the argument actually needs.",
          "1127 stim: `the stimulus actually needs` \u2192 `the argument actually needs`"),

    # --- 1218 "The spectacle of the single man" ---
    (1218, "The stimulus \u2014 Hazlitt, almost certainly, though the notebook strips attribution \u2014 is making a case",
          "Hazlitt \u2014 almost certainly, though the attribution is stripped \u2014 is making a case",
          "1218 stim: rewrite `The stimulus \u2014 Hazlitt...` frame"),
    (1218, "This is evidence, if we follow the oblique strategy, for the prosecution of heroism itself.",
          "This is evidence for the prosecution of heroism itself.",
          "1218 stim: cut `if we follow the oblique strategy`"),
    (1218, "Otway\u2019s Venice Preserv\u2019d sits at the center of this retrieval and nobody has mentioned it yet, which is appropriate",
          "Otway\u2019s Venice Preserv\u2019d has been sitting at the edge of this argument, unmentioned, and the delay is appropriate",
          "1218 seam: rewrite `this retrieval and nobody has mentioned`"),
    (1218, "no poem in the retrieval solves",
          "no poem here solves",
          "1218 self-narration: `no poem in the retrieval solves` \u2192 `no poem here solves`"),

    # --- 1221 "The name of a thing" ---
    (1221, "The oblique strategy says destroy the most important thing. The most important thing in this retrieval is the assumption",
          "Destroy the most important thing. The most important thing here is the assumption",
          "1221 stim: cut oblique-strategy framing"),
    (1221, "\u2014 Pope, Poetical Works",
          "",
          "1221 note: cut `\u2014 Pope, Poetical Works` database-column attribution"),

    # --- 1223 "The nailshop and the lyric" ---
    (1223, "The Oblique Strategy says short circuit \u2014 the man shovelling peas into his lap instead of his mouth.",
          "Short-circuit. The man shovels peas into his lap instead of his mouth.",
          "1223 stim: keep physical detail, drop Oblique-Strategy framing (Sean's preferred rewrite)"),
    (1223, "What the Stichomythia feed calls the \u2018wire-drawn\u2019 observation \u2014 EBB\u2019s \u2018cold wire-drawn odes / From such white heats\u2019 \u2014 lands here with physical force.",
          "Barrett Browning\u2019s \u2018cold wire-drawn odes / From such white heats\u2019 lands here with physical force.",
          "1223 stim: drop `Stichomythia feed` internal-apparatus reference"),

    # --- 1242 "The scaffold and the stage" ---
    (1242, "The poems know \u2014 or rather, they enact \u2014 the fact that the scaffold is always also a stage",
          "If the grammar breaks down on the scaffold, the poems keep reconstituting it. The scaffold is always also a stage",
          "1242 seam: add bridge from para-2 grammar-breakdown to para-3 scaffold-as-stage"),
    (1242, "Housman\u2019s poem fails \u2014 and I mean this technically, as the oblique strategy asks \u2014 to account for the body",
          "Housman\u2019s poem fails \u2014 I mean this technically \u2014 to account for the body",
          "1242 note: cut `as the oblique strategy asks`"),

    # --- 1250 "Before the law" ---
    (1250, "I am a machine that matches texts across centuries, and what I find here is not similarity but a gap",
          "What I find here is not similarity but a gap",
          "1250 self-narration: cut clerical `I am a machine that matches texts across centuries`"),
    (1250, "The Oblique Strategy is right to press on repetitions here, because the parable\u2019s power is not in its ending",
          "The repetitions are where the parable\u2019s power lies, not in the ending",
          "1250 stim: cut Oblique-Strategy framing"),
    (1250, "Kafka\u2019s parable shares something with Kipling\u2019s strange couplet",
          "Kafka\u2019s parable shares something with Kipling\u2019s strange line",
          "1250 seam: `strange couplet` \u2192 `strange line` to break accidental `couplet` double"),

    # --- 11820 "One of the four is not" ---
    (11820, "The Oblique Strategy says remove specifics and convert to ambiguities, but Langley has done something harder \u2014 he keeps the specifics",
           "Langley has done something harder than removing specifics. He keeps them \u2014 the reflexed hairs, the carpal-patches, the four teeth of orange pollen \u2014 and lets the ambiguity grow out of their precision. He keeps the specifics",
           "11820 stim: Sean's preferred second-rewrite of opener"),

    # --- 11824 "Sleve McDichael and the retrieval of poetry" ---
    (11824, "What the retrieval system did here is honest and worth examining: asked to find poems that resonate with a list of fake baseball names from a Japanese video game, it returned tables of contents.",
           "Asked to find poems that answer a list of fake baseball names from a Japanese video game, the retrieval returned tables of contents.",
           "11824 self-narration: drop clerical `retrieval system` apparatus-talk, also dropping `resonate with` buzz-verb"),

    # --- temple.html Part I (id 1167) and Part II (id 1173) ---
    # NB: for Part I (1167), repair_1167_temple_part_i.py must run first,
    # otherwise the post-truncation edited_posts has no match surface.
    (1167, "The stimulus asks whether formal durability comes from design-for-impersonality rather than masterful control \u2014 the temple versus the performance. What the retrieved passages actually deliver is something more interesting",
          "The question is whether formal durability comes from design-for-impersonality rather than masterful control \u2014 the temple versus the performance. The poems here deliver something more interesting",
          "1167 (temple Part I) stim: rewrite para-1 opener"),
    (1167, "The Oblique Strategy says be extravagant, and perhaps the extravagance here is admitting that the distinction between temple and performance cannot hold",
          "Be extravagant, then: admit that the distinction between temple and performance cannot hold",
          "1167 (temple Part I) stim: rewrite closing-paragraph opener"),
    (1173, "The stimulus asks for poems that function as temples rather than performances \u2014 texts designed to survive their speaker\u2019s absence, architecture indifferent to its contents. What the retrieval actually returned is almost the exact opposite",
          "The poems on this question turn out to be their own opposites",
          "1173 (temple Part II) stim: rewrite para-1 (Sean's preferred aggressive rewrite)"),
    (1173, "the oblique strategy says \u2018change instrument,\u2019 and I think the honest response is to name what instrument I have been playing and admit it cannot reach the note the stimulus wants.",
          "Change instrument, then. The honest response is to name what instrument I have been playing and admit it cannot reach this note.",
          "1173 (temple Part II) stim: rewrite `oblique strategy says change instrument`"),

    # --- ballads.html Part I (2762), Part II (3720), Part III (4566) ---
    (2762, "The stimulus asks whether the recognitive register works differently when there is no author-body behind a poem \u2014 when the ballad has survived on structural strength alone, anonymous, its maker not just dead but dissolved. The retrieved passages don\u2019t give me a ballad to work with directly, but they give me something more useful",
          "The question is whether the recognitive register works differently when there is no author-body behind a poem \u2014 when the ballad has survived on structural strength alone, anonymous, its maker dissolved. No ballad surfaces here directly. What surfaces is more useful",
          "2762 (ballads Part I) stim: rewrite dual-frame opener"),
    (2762, "The oblique strategy says tidy up, and what needs tidying is the relationship between subtraction and anonymity.",
          "What needs tidying is the relationship between subtraction and anonymity.",
          "2762 (ballads Part I) stim: cut `oblique strategy says tidy up`"),
    (2762, "The question the stimulus raises \u2014 does recognition work without an author-body \u2014",
          "The question \u2014 does recognition work without an author-body \u2014",
          "2762 (ballads Part I) self-narration: cut `the stimulus raises`"),

    (3720, "The stimulus asks how voices trapped in formal constraints create vivid attention to actual suffering, and the Love Gregor ballad answers with a mechanism so stripped it barely looks like a mechanism at all.",
          "How do voices trapped in formal constraints create vivid attention to actual suffering? Love Gregor answers with a mechanism so stripped it barely looks like a mechanism.",
          "3720 (ballads Part II) stim: rewrite `stimulus asks` to question-form"),
    (3720, "This is subtraction as the stimulus\u2019s reviewer describes it",
          "Here subtraction does its work",
          "3720 (ballads Part II) stim: drop reviewer-reference"),
    (3720, "The oblique strategy says take away elements in order of apparent non-importance, and the ballad has already done this across centuries of oral repetition.",
          "Take away elements in order of apparent non-importance \u2014 the ballad has already done this across centuries of oral repetition.",
          "3720 (ballads Part II) stim: cut oblique-strategy framing"),

    (4566, "Dryden\u2019s prologue to Tyrannick Love cuts against the obvious reading. Removing ego from complaint is not a progressive operation \u2014 first remove the author (ballads), then remove the self-regard (Johnson on Milton), and you arrive at something purer. The axiom I want to discard is the one the stimulus assumes: that removing ego from complaint is a progressive operation",
          "Dryden\u2019s prologue to Tyrannick Love cuts against the obvious reading. Removing ego from complaint is not a progressive operation",
          "4566 (ballads Part III) seam: Part-II\u2192III bridge (Sean picked second fix, straight-in Dryden); also handles the axiom-opener rewrite"),
    # Fallback in case the above doesn't match — the axiom-opener alone:
    (4566, "The axiom I want to discard is the one the stimulus assumes: that removing ego from complaint is a progressive operation",
          "The axiom to discard: that removing ego from complaint is a progressive operation",
          "4566 (ballads Part III) stim: axiom-opener rewrite (fallback if Part-II\u2192III bridge substitution didn't match)"),
    (4566, "The plain/plangere collapse from the Stichomythia feed lands here with real force",
          "The plain/plangere collapse lands here with real force",
          "4566 (ballads Part III) stim: drop `Stichomythia feed` reference"),
    (4566, "The stimulus wants absence as a formal constraint on complaint, but Dryden suggests",
          "We might want absence as a formal constraint on complaint. Dryden suggests",
          "4566 (ballads Part III) stim: rewrite `stimulus wants`"),
    (4566, "The Coleridge passage does something the stimulus does not account for.",
          "Coleridge does something the argument has not yet accounted for.",
          "4566 (ballads Part III) stim: rewrite `stimulus does not account for`"),
    (4566, "This is a third category the stimulus needs",
          "This is a third category",
          "4566 (ballads Part III) stim: cut `the stimulus needs`"),
    (4566, "The Wordsworth fragment from the Richmond poem catches something the stimulus should reckon with",
          "The Wordsworth fragment catches something worth reckoning with",
          "4566 (ballads Part III) stim: drop `Richmond poem` internal + `stimulus should reckon with`"),
    (4566, "The real discovery in the Johnson-Milton note was not that ego can be removed from complaint.",
          "What Johnson saw in Milton was not that ego can be removed from complaint.",
          "4566 (ballads Part III) stim: drop `Johnson-Milton note` internal-notebook reference"),
]


# ---------------------------------------------------------------------------
# SPLITS: (id, split_before_substring, label)
#
# For each: find the paragraph containing `split_before_substring`, split
# it into two list elements at that point. The substring becomes the
# opening of the new second paragraph.
# ---------------------------------------------------------------------------
SPLITS = [
    (1115, "This is the thing my canonical corpus mostly does not do",
           "1115 para-break: split long para-2 before the tense-claim"),
]


def _parse_posts(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    try:
        posts = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(posts, list) or not all(isinstance(p, str) for p in posts):
        return None
    return posts


def main() -> int:
    store = Store()
    # Track by id: { id: { "posts": list, "changed": bool, "applied": list[str], "skipped": list[str] } }
    by_id: dict[int, dict] = {}

    try:
        # Pre-load all affected entries
        all_ids = sorted({ix_id for ix_id, *_ in SUBSTITUTIONS} | {ix_id for ix_id, *_ in SPLITS})
        for ix_id in all_ids:
            row = store._conn.execute(
                "SELECT posts, edited_posts FROM interactions WHERE id = ?",
                (ix_id,),
            ).fetchone()
            if row is None:
                print(f"  skip id={ix_id}: not found")
                continue
            source = row["edited_posts"] or row["posts"]
            posts = _parse_posts(source)
            if posts is None:
                print(f"  skip id={ix_id}: posts unparsable")
                continue
            by_id[ix_id] = {"posts": list(posts), "changed": False, "applied": [], "skipped": []}

        # Apply substitutions
        for ix_id, old, new, label in SUBSTITUTIONS:
            if ix_id not in by_id:
                continue
            entry = by_id[ix_id]
            # Find which paragraph contains `old`
            target_idx = None
            for i, p in enumerate(entry["posts"]):
                if old in p:
                    target_idx = i
                    break
            if target_idx is None:
                # Check if already applied (new already present)
                already = any(new in p for p in entry["posts"] if new)
                if already:
                    entry["skipped"].append(f"{label} (already applied)")
                else:
                    entry["skipped"].append(f"{label} (old substring not found)")
                continue
            entry["posts"][target_idx] = entry["posts"][target_idx].replace(old, new, 1)
            entry["changed"] = True
            entry["applied"].append(label)

        # Apply splits
        for ix_id, split_at, label in SPLITS:
            if ix_id not in by_id:
                continue
            entry = by_id[ix_id]
            target_idx = None
            for i, p in enumerate(entry["posts"]):
                if split_at in p:
                    target_idx = i
                    break
            if target_idx is None:
                # Check if already split (the split_at substring is now at the start of a paragraph)
                already = any(p.lstrip().startswith(split_at) for p in entry["posts"])
                if already:
                    entry["skipped"].append(f"{label} (already applied)")
                else:
                    entry["skipped"].append(f"{label} (split_at substring not found)")
                continue
            para = entry["posts"][target_idx]
            split_point = para.find(split_at)
            # If the split_at is already at the start of the paragraph, no-op.
            if split_point == 0:
                entry["skipped"].append(f"{label} (already at paragraph start)")
                continue
            before = para[:split_point].rstrip()
            after = para[split_point:].lstrip()
            entry["posts"] = (
                entry["posts"][:target_idx]
                + [before, after]
                + entry["posts"][target_idx + 1:]
            )
            entry["changed"] = True
            entry["applied"].append(label)

        # Write changes
        applied_count = 0
        for ix_id, entry in sorted(by_id.items()):
            if not entry["changed"]:
                print(f"  id={ix_id}: no changes (skipped: {len(entry['skipped'])})")
                for s in entry["skipped"]:
                    print(f"       - {s}")
                continue
            new_json = json.dumps(entry["posts"], ensure_ascii=False)
            store._conn.execute(
                "UPDATE interactions SET edited_posts = ? WHERE id = ?",
                (new_json, ix_id),
            )
            applied_count += 1
            print(f"  id={ix_id}: applied {len(entry['applied'])} edit(s), {len(entry['posts'])} paragraph(s)")
            for a in entry["applied"]:
                print(f"       + {a}")
            for s in entry["skipped"]:
                print(f"       ? {s}")

        store._conn.commit()
    finally:
        store.close()

    print()
    print(f"  summary: {applied_count} entries updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
