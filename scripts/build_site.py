#!/usr/bin/env python3
"""Static site generator — reads interactions DB, renders Jinja2 templates,
optionally pushes to stwalsh/lucubrator on GitHub Pages.

Usage:
    ./venv/bin/python scripts/build_site.py              # build + push
    ./venv/bin/python scripts/build_site.py --no-push    # local preview only
"""

import argparse
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Allow running as `python scripts/build_site.py` from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jinja2 import Environment, FileSystemLoader

from src.store import Store

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "site"
BUILD_DIR = Path(__file__).resolve().parent.parent / "data" / "site_build"
REPO_URL = "https://github.com/stwalsh/lucubrator.git"


def _make_slug(interaction: dict) -> str:
    """Generate a URL slug from timestamp + id, e.g. '2026-03-10-001'."""
    ts = interaction.get("timestamp", "")
    date = ts[:10] if len(ts) >= 10 else "undated"
    row_id = interaction.get("id", 0)
    return f"{date}-{row_id:03d}"


def _get_passage_text(interaction: dict, store: Store) -> dict | None:
    """Enrich passage_used with the actual verse text from ChromaDB if available."""
    pu = interaction.get("passage_used")
    if not pu or not isinstance(pu, dict):
        return None

    chunk_id = pu.get("chunk_id", "")
    result = {
        "chunk_id": chunk_id,
        "poet": pu.get("poet", ""),
        "poem_title": pu.get("poem_title", ""),
        "date": pu.get("date", ""),
        "text": pu.get("text", ""),
    }

    # If no text in the interaction record, try passage_notes
    if not result["text"]:
        note = store.get_passage_note(chunk_id)
        if note:
            result["poet"] = result["poet"] or note.get("poet", "")
            result["poem_title"] = result["poem_title"] or note.get("poem_title", "")

    return result


def build(store: Store) -> Path:
    """Render the site into BUILD_DIR. Returns the build path."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
    )

    # Fetch data (exclude manual test runs)
    interactions = [
        ix for ix in store.get_posted_interactions(include_dry_run=True)
        if ix.get("source") != "test"
    ]
    reflections = store.get_all_reflections()

    # Prepare entries
    entries = []
    for ix in interactions:
        entry = {
            "id": ix["id"],
            "slug": _make_slug(ix),
            "date": ix["timestamp"][:10] if ix.get("timestamp") else "undated",
            "timestamp": ix.get("timestamp", ""),
            "stimulus_text": ix.get("stimulus_text", ""),
            "stimulus_author": ix.get("stimulus_author", ""),
            "source": ix.get("source", ""),
            "posts": ix.get("posts") or [],
            "passage_used": _get_passage_text(ix, store),
            "triage_reason": ix.get("triage_reason", ""),
            "the_problem": ix.get("the_problem", ""),
            "triage_queries": ix.get("triage_queries") or [],
            "composition_mode": ix.get("composition_mode", ""),
        }
        entries.append(entry)

    # Group by date (reverse chronological for index)
    entries_by_date_dict = defaultdict(list)
    for e in entries:
        entries_by_date_dict[e["date"]].append(e)
    entries_by_date = sorted(entries_by_date_dict.items(), reverse=True)

    # Group reflections by date
    reflections_by_date = {}
    for r in reflections:
        date = r["timestamp"][:10] if r.get("timestamp") else ""
        if date:
            reflections_by_date[date] = r

    # Clean + create build dir
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True)
    (BUILD_DIR / "entries").mkdir()
    (BUILD_DIR / "reflections").mkdir()

    # Copy CSS
    shutil.copy(TEMPLATE_DIR / "style.css", BUILD_DIR / "style.css")

    # Render index
    index_tpl = env.get_template("index.html")
    (BUILD_DIR / "index.html").write_text(
        index_tpl.render(
            root="",
            entries_by_date=entries_by_date,
            reflections_by_date=reflections_by_date,
        ),
        encoding="utf-8",
    )

    # Render individual entries
    entry_tpl = env.get_template("entry.html")
    for entry in entries:
        html = entry_tpl.render(root="../", entry=entry)
        (BUILD_DIR / "entries" / f"{entry['slug']}.html").write_text(html, encoding="utf-8")

    # Render reflections
    reflection_tpl = env.get_template("reflection.html")
    for date, refl in reflections_by_date.items():
        html = reflection_tpl.render(root="../", date=date, reflection=refl)
        (BUILD_DIR / "reflections" / f"{date}.html").write_text(html, encoding="utf-8")

    print(f"  Built {len(entries)} entries, {len(reflections_by_date)} reflections -> {BUILD_DIR}")
    return BUILD_DIR


def push(build_dir: Path):
    """Clone/pull the lucubrator repo, copy build output, commit and push."""
    repo_dir = build_dir.parent / "lucubrator_repo"

    if (repo_dir / ".git").exists():
        print("  Pulling latest from origin...")
        subprocess.run(["git", "pull", "--ff-only"], cwd=repo_dir, check=True)
    else:
        print(f"  Cloning {REPO_URL}...")
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        subprocess.run(["git", "clone", REPO_URL, str(repo_dir)], check=True)

    # Clear existing content (except .git)
    for item in repo_dir.iterdir():
        if item.name == ".git":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    # Copy build output
    for item in build_dir.iterdir():
        dest = repo_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    # Add .nojekyll for GitHub Pages
    (repo_dir / ".nojekyll").touch()

    # Commit and push
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)

    # Check if there are changes
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=repo_dir, capture_output=True
    )
    if result.returncode == 0:
        print("  No changes to push.")
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subprocess.run(
        ["git", "commit", "-m", f"Update site — {now}"],
        cwd=repo_dir, check=True,
    )
    subprocess.run(["git", "push"], cwd=repo_dir, check=True)
    print("  Pushed to GitHub Pages.")


def main():
    parser = argparse.ArgumentParser(description="Lucubrator — static site generator")
    parser.add_argument("--no-push", action="store_true", help="Build only, don't push to GitHub")
    parser.add_argument("--db", type=str, default=None, help="Path to interactions.db")
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
