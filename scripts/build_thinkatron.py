#!/usr/bin/env python3
"""thinkatron.review — static site generator for hand-picked entries.

Reads interactions where `featured = 1`, renders a minimal literary-journal
layout, and optionally pushes to stwalsh/thinkatron (deployed via Netlify).

Usage:
    ./venv/bin/python scripts/build_thinkatron.py              # build + push
    ./venv/bin/python scripts/build_thinkatron.py --no-push    # local preview
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jinja2 import Environment, FileSystemLoader

from src.store import Store

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "thinkatron"
BUILD_DIR = Path(__file__).resolve().parent.parent / "data" / "thinkatron_build"
OVERRIDES_PATH = Path(__file__).resolve().parent.parent / "config" / "thinkatron_overrides.json"
REPO_URL = "git@github.com:stwalsh/thinkatron.git"


def _load_overrides_raw() -> dict:
    if not OVERRIDES_PATH.exists():
        return {}
    try:
        return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  WARNING: could not parse {OVERRIDES_PATH}: {e}")
        return {}


def _load_overrides() -> dict:
    data = _load_overrides_raw()
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}


def _load_groups() -> dict:
    data = _load_overrides_raw()
    groups = data.get("_groups", {})
    return groups if isinstance(groups, dict) else {}


def _roman(n: int) -> str:
    numerals = [("X", 10), ("IX", 9), ("V", 5), ("IV", 4), ("I", 1)]
    out = ""
    for sym, val in numerals:
        while n >= val:
            out += sym
            n -= val
    return out


def _slug(ix: dict) -> str:
    ts = ix.get("timestamp", "")
    date = ts[:10] if len(ts) >= 10 else "undated"
    return f"{date}-{ix.get('id', 0):03d}"


def _clean_stim(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"^\[(?:contemplate|compare|engage_self)\]\s*", "", text).strip()


def _passage(ix: dict) -> dict | None:
    pu = ix.get("passage_used")
    if isinstance(pu, str):
        try:
            pu = json.loads(pu)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(pu, dict):
        return None
    return {
        "poet": pu.get("poet", ""),
        "poem_title": pu.get("poem_title", ""),
        "date": pu.get("date", ""),
        "text": pu.get("text", ""),
    }


def _fetch_featured(store: Store) -> list[dict]:
    rows = store._conn.execute(
        "SELECT * FROM interactions WHERE featured = 1 ORDER BY timestamp DESC"
    ).fetchall()
    out = []
    for row in rows:
        ix = dict(row)
        for field in ("posts", "edited_posts", "passage_used", "passages_retrieved"):
            val = ix.get(field)
            if isinstance(val, str):
                try:
                    ix[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        out.append(ix)
    return out


def build(store: Store) -> Path:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)

    rows = _fetch_featured(store)
    overrides = _load_overrides()
    groups = _load_groups()

    # Map interaction id (as str) -> (group_id, part_index in reading order).
    id_to_group = {}
    for gid, g in groups.items():
        for idx, pid in enumerate(g.get("ids", [])):
            id_to_group[str(pid)] = (gid, idx)

    # Bucket rows into solo entries and group parts.
    solo_rows = []
    group_rows: dict[str, list[tuple[int, dict]]] = {}
    for ix in rows:
        ikey = str(ix["id"])
        if ikey in id_to_group:
            gid, part_idx = id_to_group[ikey]
            group_rows.setdefault(gid, []).append((part_idx, ix))
        else:
            solo_rows.append(ix)

    def _part(ix: dict, roman: str | None) -> dict:
        posts = ix.get("edited_posts") or ix.get("posts") or []
        return {
            "roman": roman,
            "passage_used": _passage(ix),
            "posts": posts,
        }

    def _lede(parts: list[dict]) -> str:
        for p in parts:
            if p["posts"]:
                first = p["posts"][0]
                return first[:140] + ("…" if len(first) > 140 else "")
        return ""

    entries = []

    for ix in solo_rows:
        ov = overrides.get(str(ix["id"]), {})
        tags = ov.get("author_tags") or []
        if isinstance(tags, str):
            tags = [tags]
        parts = [_part(ix, None)]
        entries.append({
            "id": ix["id"],
            "slug": _slug(ix),
            "date": ix["timestamp"][:10] if ix.get("timestamp") else "undated",
            "head": ov.get("head") or None,
            "stand": ov.get("stand") or None,
            "author_tags": tags,
            "lede": _lede(parts),
            "parts": parts,
        })

    for gid, raw_parts in group_rows.items():
        raw_parts.sort(key=lambda t: t[0])
        g = groups[gid]
        parts = [_part(ix, _roman(i + 1)) for i, (_, ix) in enumerate(raw_parts)]
        tags = g.get("author_tags") or []
        if isinstance(tags, str):
            tags = [tags]
        latest_ts = max((ix.get("timestamp", "") for _, ix in raw_parts), default="")
        entries.append({
            "id": gid,
            "slug": gid,
            "date": latest_ts[:10] if latest_ts else "undated",
            "head": g.get("head") or None,
            "stand": g.get("stand") or None,
            "author_tags": tags,
            "lede": _lede(parts),
            "parts": parts,
        })

    entries.sort(key=lambda e: e["date"], reverse=True)

    # Previous (older) / Next (newer) — entries are reverse-chronological,
    # so older = higher index, newer = lower index.
    for i, e in enumerate(entries):
        e["prev_slug"] = entries[i + 1]["slug"] if i + 1 < len(entries) else None
        e["next_slug"] = entries[i - 1]["slug"] if i > 0 else None

    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True)
    (BUILD_DIR / "entries").mkdir()

    shutil.copy(TEMPLATE_DIR / "style.css", BUILD_DIR / "style.css")

    (BUILD_DIR / "index.html").write_text(
        env.get_template("index.html").render(root="", entries=entries),
        encoding="utf-8",
    )
    (BUILD_DIR / "about.html").write_text(
        env.get_template("about.html").render(root=""),
        encoding="utf-8",
    )
    (BUILD_DIR / "colophon.html").write_text(
        env.get_template("colophon.html").render(root=""),
        encoding="utf-8",
    )

    entry_tpl = env.get_template("entry.html")
    for e in entries:
        (BUILD_DIR / "entries" / f"{e['slug']}.html").write_text(
            entry_tpl.render(root="../", entry=e),
            encoding="utf-8",
        )

    print(f"  Built {len(entries)} featured entries -> {BUILD_DIR}")
    return BUILD_DIR


def push(build_dir: Path):
    repo_dir = build_dir.parent / "thinkatron_repo"

    if (repo_dir / ".git").exists():
        subprocess.run(["git", "pull", "--ff-only"], cwd=repo_dir, check=True)
    else:
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        subprocess.run(["git", "clone", REPO_URL, str(repo_dir)], check=True)

    # Preserve these files in the repo root; overwrite everything else.
    preserve = {".git", "netlify.toml", ".gitignore", "README.md", "CLAUDE.md"}
    for item in repo_dir.iterdir():
        if item.name in preserve:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    for item in build_dir.iterdir():
        dest = repo_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=repo_dir, capture_output=True
    )
    if result.returncode == 0:
        print("  No changes to push.")
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subprocess.run(["git", "commit", "-m", f"Update thinkatron — {now}"], cwd=repo_dir, check=True)
    subprocess.run(["git", "push"], cwd=repo_dir, check=True)
    print("  Pushed to thinkatron.")


def main():
    parser = argparse.ArgumentParser(description="thinkatron.review — static site generator")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--db", type=str, default=None)
    args = parser.parse_args()

    store = Store(db_path=args.db) if args.db else Store()
    try:
        build_dir = build(store)
        if not args.no_push:
            push(build_dir)
        else:
            print(f"  Preview: open {build_dir / 'index.html'}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
