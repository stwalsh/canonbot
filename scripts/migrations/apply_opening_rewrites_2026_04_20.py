#!/usr/bin/env python3
"""One-shot: apply copy-sub opening-pass rewrites to featured entries.

Session: 2026-04-20. The copy-sub was run in critique mode with the opening-pass
focus (strip process narration, begin cold). Sean approved the proposed rewrites.

This script writes the new opening paragraph to `edited_posts` so the build
script picks it up on the next rebuild. Original `posts` field is left untouched.

Idempotent: re-running updates edited_posts to the same content. Safe to run
more than once.

Run:
    cd ~/Desktop/canonbot
    ./venv/bin/python scripts/apply_opening_rewrites_2026_04_20.py

Then rebuild:
    ./venv/bin/python scripts/build_thinkatron.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.store import Store


# id (as int) -> new opening paragraph replacing paragraph 0 of `posts`.
REWRITES: dict[int, str] = {
    # 1031 — The Scholar-Gipsy and the stolen machine
    1031: (
        "The Scholar-Gipsy leaves Oxford to learn \u201carts to rule as they desired / "
        "The workings of men\u2019s brains\u201d from the Romani, and promises he will "
        "\u201cto the world impart\u201d the secret \u201cwhen fully learn\u2019d.\u201d "
        "But the poem\u2019s entire structure is organised around the fact that he "
        "never comes back to impart it."
    ),
    # 1115 — The 4th Chamber and the compression of time
    1115: (
        "Ghostface moves from \u201csky-blue Bally kid in \u201883\u201d to "
        "\u201cConstantine the Great, Henry VIII / Built with Genghis Khan\u201d in the "
        "space of a verse, and this is not allusion in the literary-critical sense "
        "\u2014 it is not a poet gesturing toward a predecessor and expecting the "
        "reader to feel the distance. It is annexation."
    ),
    # 1127 — When the apparatus collapses
    1127: (
        "Herbert\u2019s \u201cGrief\u201d is the test case, and it is a harder case than "
        "it first appears. The poem spends four stanzas building an elaborate "
        "hydraulic conceit \u2014 eyes as springs, veins sucking up rivers, the body as "
        "a \u201clittle World\u201d with \u201ctwo little Spouts\u201d insufficient for "
        "the grief\u2019s scale."
    ),
    # 1218 — The spectacle of the single man
    1218: (
        "Admiration works as a political narcotic. We watch the single man who "
        "\u201ccomes forward to brave their cries\u201d and our attention transfers "
        "from the suffering of the many to the spectacle of his will. The multitude "
        "becomes \u201cmiserable rogues\u201d the instant a protagonist appears."
    ),
    # 1242 — The scaffold and the stage
    1242: (
        "In 1427 Paris, every institutional role at a hanging failed simultaneously "
        "\u2014 the official who should ensure spiritual rights denied them, the "
        "executioner who should kill cleanly botched it, the scaffold that should "
        "end suffering extended it \u2014 and the only person who performed his "
        "function correctly was the condemned man, who dragged himself back up to "
        "be hanged again."
    ),
    # 11824 — Sleve McDichael and the retrieval of poetry
    11824: (
        "Asked to find poems that resonate with a list of fake baseball names from "
        "a Japanese video game, the retrieval returned tables of contents. Lists of "
        "real names. The algorithm found the nearest thing in the corpus to a roster "
        "of proper nouns and gave back a roster of proper nouns \u2014 Tobias "
        "Smollett, Ford Madox Ford, Geoffrey Chaucer, Diego Vel\u00e1zquez."
    ),
    # 1167 — The temple and the performance, Part I (Marvell)
    1167: (
        "Marvell\u2019s elegy for Cromwell is doing precisely the thing one would "
        "expect of an impersonal monument while simultaneously undermining it. "
        "\u201cWithout our help, thy Memory is safe\u201d \u2014 the fame is "
        "self-sustaining, the roses and perfumes are \u201cofficious folly,\u201d the "
        "poets\u2019 spices are surplus to requirements because \u201cThat need not "
        "be imbalm\u2019d, which of it self is sweet.\u201d"
    ),
    # 1173 — The temple and the performance, Part II (Fitzgeffrey)
    1173: (
        "Fitzgeffrey\u2019s \u201cEpilogue\u201d is a negative confession that cannot "
        "stop confessing \u2014 \u201cI Am no Poet! (yet I doe not know / Why I should "
        "not: or why I should be so)\u201d \u2014 and every denial of poetic identity "
        "is executed in competent verse, which means the denial is self-cancelling "
        "at the level of form."
    ),
    # 2762 — Ego and the ballads, Part I (Wordsworth's London walls)
    2762: (
        "Wordsworth\u2019s London places ballads as physical objects \u2014 "
        "\u201cfiles of ballads dangle from dead walls\u201d \u2014 and the deadness "
        "of those walls is doing real work, because the ballads are dangling there "
        "like shed skins, present but emptied of the voice that produced them."
    ),
    # 3720 — Ego and the ballads, Part II (Love Gregor)
    3720: (
        "\u201cO wha will shoe my fu fair foot? / And wha will glove my hand? / "
        "And wha will kaim my yellow hair\u201d \u2014 the entire Love Gregor stanza "
        "is a list of services no one will perform. Each question is an absence "
        "wearing the syntax of a practical problem."
    ),
    # 4566 — Ego and the ballads, Part III (Dryden's Tyrannick Love)
    4566: (
        "Dryden\u2019s prologue to \u201cTyrannick Love\u201d looks like a renunciation "
        "of ego. \u201cSelf-love (which never rightly understood) / Makes Poets still "
        "conclude their Plays are good\u201d \u2014 a prologue that performs modesty. "
        "But the entire mechanism of the poem is self-regard disguised as its critique."
    ),
}


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
    applied = 0
    skipped = 0
    warnings: list[str] = []
    try:
        for ix_id, new_opening in REWRITES.items():
            row = store._conn.execute(
                "SELECT posts, edited_posts FROM interactions WHERE id = ?",
                (ix_id,),
            ).fetchone()
            if row is None:
                print(f"  skip id={ix_id}: not found in DB")
                skipped += 1
                continue
            # Work from edited_posts if already set (so re-runs are idempotent),
            # otherwise from the original posts.
            source = row["edited_posts"] or row["posts"]
            posts = _parse_posts(source)
            if posts is None:
                print(f"  skip id={ix_id}: posts field unparsable or empty")
                skipped += 1
                continue
            if not posts:
                print(f"  skip id={ix_id}: posts list is empty")
                skipped += 1
                continue

            # Cheap duplication check: see if the opening's first 40 chars appear
            # verbatim in any later paragraph.
            sniff = new_opening[:40]
            for i, later in enumerate(posts[1:], start=1):
                if sniff in later:
                    warnings.append(
                        f"id={ix_id}: opening sniff appears in posts[{i}] "
                        f"(possible duplication — review after rebuild)"
                    )
                    break

            new_posts = [new_opening, *posts[1:]]
            new_json = json.dumps(new_posts, ensure_ascii=False)
            store._conn.execute(
                "UPDATE interactions SET edited_posts = ? WHERE id = ?",
                (new_json, ix_id),
            )
            applied += 1
            print(f"  applied id={ix_id}: opening rewritten")

        store._conn.commit()
    finally:
        store.close()

    print()
    print(f"  summary: {applied} applied, {skipped} skipped")
    if warnings:
        print()
        print("  warnings (review after next build):")
        for w in warnings:
            print(f"    - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
