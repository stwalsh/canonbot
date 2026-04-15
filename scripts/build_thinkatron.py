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
REPO_URL = "git@github.com:stwalsh/thinkatron.git"


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
    entries = []
    for ix in rows:
        posts = ix.get("edited_posts") or ix.get("posts") or []
        first = posts[0] if posts else _clean_stim(ix.get("stimulus_text", ""))
        entries.append({
            "id": ix["id"],
            "slug": _slug(ix),
            "date": ix["timestamp"][:10] if ix.get("timestamp") else "undated",
            "lede": first[:140] + ("…" if len(first) > 140 else ""),
            "posts": posts,
            "passage_used": _passage(ix),
        })

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
    preserve = {".git", "netlify.toml", ".gitignore", "README.md"}
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
