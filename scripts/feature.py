#!/usr/bin/env python3
"""Mark or unmark interactions as featured on thinkatron.review.

    ./venv/bin/python scripts/feature.py 42 57 103     # feature these IDs
    ./venv/bin/python scripts/feature.py --unfeature 42
    ./venv/bin/python scripts/feature.py --list
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.store import Store


def main():
    p = argparse.ArgumentParser(description="Feature/unfeature entries for thinkatron.review")
    p.add_argument("ids", nargs="*", type=int, help="interaction IDs")
    p.add_argument("--unfeature", action="store_true", help="clear featured flag instead")
    p.add_argument("--list", action="store_true", help="list currently featured entries")
    p.add_argument("--db", type=str, default=None)
    args = p.parse_args()

    store = Store(db_path=args.db) if args.db else Store()
    try:
        if args.list:
            rows = store._conn.execute(
                "SELECT id, timestamp, source FROM interactions WHERE featured = 1 ORDER BY timestamp DESC"
            ).fetchall()
            if not rows:
                print("(none)")
            for r in rows:
                print(f"  {r['id']:>5}  {r['timestamp'][:16]}  {r['source']}")
            return

        if not args.ids:
            p.error("provide interaction IDs, or use --list")

        value = 0 if args.unfeature else 1
        placeholders = ",".join("?" * len(args.ids))
        store._conn.execute(
            f"UPDATE interactions SET featured = ? WHERE id IN ({placeholders})",
            [value] + args.ids,
        )
        store._conn.commit()
        verb = "Unfeatured" if args.unfeature else "Featured"
        print(f"{verb} {len(args.ids)} entries.")
    finally:
        store.close()


if __name__ == "__main__":
    main()
