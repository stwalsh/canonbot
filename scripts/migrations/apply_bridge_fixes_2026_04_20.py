#!/usr/bin/env python3
"""Surgical bridge fixes — connective tissue broken by opening-pass rewrites.

Session: 2026-04-20 late. After the opening-pass migration landed (11 pieces
had their first paragraph rewritten to drop scaffolding), some paragraph-2
openings no longer follow from the new paragraph-1. They assumed the original
scaffolding's framing and read as non sequiturs once the scaffolding was cut.

This script is a targeted second pass that fixes individual seams without
running a full throughlines sweep. Each fix is a surgical replace at the
start of a specific paragraph within a specific entry's `edited_posts`.

Idempotent: each fix is only applied if the target string is still present.
Safe to run more than once.

Run:
    ./venv/bin/python scripts/migrations/apply_bridge_fixes_2026_04_20.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.store import Store


# Each entry: (interaction_id, paragraph_index, old_opening_substring, new_opening_substring).
# The old substring must appear at the start of the target paragraph.
FIXES = [
    (
        1242,
        1,
        "Shelley comes closer. His Pope in The Cenci is described as a machine —",
        "Of the corpus's execution poems, Shelley comes closer than most. His Pope in The Cenci is described as a machine —",
    ),
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
    applied = 0
    skipped = 0
    try:
        for ix_id, para_idx, old_sub, new_sub in FIXES:
            row = store._conn.execute(
                "SELECT posts, edited_posts FROM interactions WHERE id = ?",
                (ix_id,),
            ).fetchone()
            if row is None:
                print(f"  skip id={ix_id}: not found in DB")
                skipped += 1
                continue
            source = row["edited_posts"] or row["posts"]
            posts = _parse_posts(source)
            if posts is None or para_idx >= len(posts):
                print(f"  skip id={ix_id}: posts unparsable or paragraph index out of range")
                skipped += 1
                continue

            target_para = posts[para_idx]
            if new_sub in target_para:
                print(f"  skip id={ix_id} para[{para_idx}]: fix already applied (idempotent)")
                skipped += 1
                continue
            if old_sub not in target_para:
                print(f"  skip id={ix_id} para[{para_idx}]: old substring not found (hand-edited since?)")
                skipped += 1
                continue

            new_para = target_para.replace(old_sub, new_sub, 1)
            new_posts = list(posts)
            new_posts[para_idx] = new_para
            new_json = json.dumps(new_posts, ensure_ascii=False)
            store._conn.execute(
                "UPDATE interactions SET edited_posts = ? WHERE id = ?",
                (new_json, ix_id),
            )
            applied += 1
            print(f"  applied id={ix_id} para[{para_idx}]: bridge sentence inserted")

        store._conn.commit()
    finally:
        store.close()

    print()
    print(f"  summary: {applied} applied, {skipped} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
