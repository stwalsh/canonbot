#!/usr/bin/env python3
"""Repair migration — restore temple Part I (id 1167) body content.

The opening-pass migration `apply_opening_rewrites_2026_04_20.py` used
the logic:

    new_posts = [new_opening] + list(posts[1:])

This assumes `posts` is a multi-element list where `[0]` is the first
paragraph and `[1:]` is subsequent paragraphs. For entry 1167 (Marvell's
elegy for Cromwell, the first half of the 'temple' diptych), the original
`posts` field stored the entire piece as a *single-element list* whose
one element was a long string containing three implied paragraphs
separated by blank lines (\\n\\n). The opening-pass migration therefore
computed `posts[1:]` as empty, and wrote just `[new_opening]` to
`edited_posts` — truncating the rest of the piece out of the rendered
output.

The original `posts` field is untouched (migrations only write to
`edited_posts`), so the source text is still on the Pi intact. This
repair migration reads from `posts` directly, splits on `\\n\\n`, replaces
only the first block with the approved rewrite, and writes a correct
multi-paragraph `edited_posts`.

Idempotent: reads from `posts` (not from `edited_posts`), so safe to
re-run. Writes the same output each time.

Lesson for future migrations: always read from `posts` and handle the
single-element-with-blank-lines case if doing paragraph-level surgery.
Note added to docs/thinkatron_editorial_style.md.

Run:
    cd ~/Desktop/canonbot
    ./venv/bin/python scripts/migrations/repair_1167_temple_part_i.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.store import Store


ENTRY_ID = 1167

# Verbatim from apply_opening_rewrites_2026_04_20.py — the sub-approved
# opening-pass rewrite for temple Part I (Marvell's elegy).
NEW_OPENING = (
    "Marvell\u2019s elegy for Cromwell is doing precisely the thing one would "
    "expect of an impersonal monument while simultaneously undermining it. "
    "\u201cWithout our help, thy Memory is safe\u201d \u2014 the fame is "
    "self-sustaining, the roses and perfumes are \u201cofficious folly,\u201d the "
    "poets\u2019 spices are surplus to requirements because \u201cThat need not "
    "be imbalm\u2019d, which of it self is sweet.\u201d"
)


def main() -> int:
    store = Store()
    try:
        row = store._conn.execute(
            "SELECT posts FROM interactions WHERE id = ?", (ENTRY_ID,)
        ).fetchone()
        if row is None:
            print(f"  skip id={ENTRY_ID}: not found in DB")
            return 0

        raw = row["posts"]
        if not raw:
            print(f"  skip id={ENTRY_ID}: posts field empty")
            return 0

        try:
            posts = json.loads(raw)
        except json.JSONDecodeError:
            print(f"  skip id={ENTRY_ID}: posts unparsable as JSON")
            return 1

        if not isinstance(posts, list) or not posts:
            print(f"  skip id={ENTRY_ID}: posts not a non-empty list")
            return 1

        # Detect and repair the single-element-with-blank-line-separated-blocks
        # case. If posts is already multi-element, just replace the first.
        if len(posts) == 1 and "\n\n" in posts[0]:
            blocks = [b.strip() for b in posts[0].split("\n\n") if b.strip()]
            new_posts = [NEW_OPENING] + blocks[1:]
            print(
                f"  id={ENTRY_ID}: single-element posts detected, "
                f"{len(blocks)} blocks extracted from original string"
            )
        else:
            new_posts = [NEW_OPENING] + list(posts[1:])
            print(
                f"  id={ENTRY_ID}: multi-element posts ({len(posts)}), "
                "standard replace"
            )

        new_json = json.dumps(new_posts, ensure_ascii=False)
        store._conn.execute(
            "UPDATE interactions SET edited_posts = ? WHERE id = ?",
            (new_json, ENTRY_ID),
        )
        store._conn.commit()
        print(
            f"  id={ENTRY_ID}: edited_posts updated with "
            f"{len(new_posts)} paragraph(s)"
        )
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
