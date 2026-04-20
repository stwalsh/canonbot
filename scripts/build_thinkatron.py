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

from markupsafe import Markup, escape
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


# Threshold: quotes with 2+ slashes (3+ verse lines) break out as block quotes.
_SLASH_THRESHOLD = 2
# Prose inset threshold: 25+ words triggers block inset for prose quotations.
_PROSE_INSET_WORDS = 25

# Match "quoted text" — Surname  (curly or straight quotes, em dash + attribution)
_ATTR_QUOTE_RE = re.compile(
    r'(?P<open>["\u201c])'           # opening quote
    r'(?P<text>[^"\u201d]+?)'        # quoted text (non-greedy)
    r'(?P<close>["\u201d])'          # closing quote
    r'\s*\u2014\s*'                  # em dash with optional spaces
    r'(?P<attr>[A-Z][A-Za-z\'. ]+?)' # attribution (starts uppercase)
    r'(?=[.,;:!?\s\u2014\)]|$)',     # followed by punctuation, space, or end
)

# Match any quoted text — curly or straight quotes.
# Straight-quote matching requires at least one space inside to avoid
# contractions or possessives triggering false matches.
_BARE_QUOTE_RE = re.compile(
    r'(?P<open>["\u201c])(?P<text>[^"\u201d]{4,}?)(?P<close>["\u201d])'
)


def _format_post(text: str) -> Markup:
    """Process a post's plain text into HTML with styled verse quotations.

    Two-pass approach:
    1. Attributed quotes ("..." — Surname) get full treatment: inline or block inset.
    2. Remaining quoted text gets italic styling (conventional for quotation
       in critical prose — covers verse fragments, titles, and terms of art).

    Bucketing: verse inset (3+ lines), prose inset (25+ words), inline italic.
    """
    def _inset_end(match_end: int) -> int:
        """Absorb trailing sentence-ending punctuation after insets."""
        if match_end < len(text) and text[match_end] in ".,;:!?":
            return match_end + 1
        return match_end

    def _render_quote(quoted: str, match_end: int) -> tuple[Markup, int]:
        """Decide inline vs verse-inset vs prose-inset for a quoted fragment."""
        slash_count = quoted.count(" / ")
        word_count = len(quoted.split())

        if slash_count >= _SLASH_THRESHOLD:
            # Verse inset: lineated
            lines = [line.strip() for line in quoted.split(" / ")]
            verse_html = "<br>\n".join(escape(line) for line in lines)
            html = Markup(
                '</p>\n<blockquote class="verse-inset"><p class="verse-lines">'
                f'{verse_html}</p></blockquote>\n<p>'
            )
            return html, _inset_end(match_end)

        if word_count >= _PROSE_INSET_WORDS:
            # Prose inset: roman block
            html = Markup(
                '</p>\n<blockquote class="prose-inset"><p>'
                f'{escape(quoted)}</p></blockquote>\n<p>'
            )
            return html, _inset_end(match_end)

        # Inline: italic, no quotes
        return Markup(f'<i class="verse">{escape(quoted)}</i>'), match_end

    # Pass 1: attributed quotes ("..." — Surname)
    attr_spans = {}
    for m in _ATTR_QUOTE_RE.finditer(text):
        html, end = _render_quote(m.group("text"), m.end())
        attr_spans[m.start()] = (end, html)

    # Pass 2: bare quoted text not already handled
    bare_spans = {}
    for m in _BARE_QUOTE_RE.finditer(text):
        if any(start <= m.start() < end for start, (end, _) in attr_spans.items()):
            continue
        html, end = _render_quote(m.group("text"), m.end())
        bare_spans[m.start()] = (end, html)

    # Merge all spans and build output
    all_spans = {**attr_spans, **bare_spans}
    if not all_spans:
        return Markup(escape(text))

    parts = []
    last_end = 0
    for start in sorted(all_spans):
        end, html = all_spans[start]
        parts.append(escape(text[last_end:start]))
        parts.append(html)
        last_end = end

    parts.append(escape(text[last_end:]))
    return Markup("".join(parts))


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
            "posts": [_format_post(p) for p in posts],
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

    fonts_src = TEMPLATE_DIR / "fonts"
    if fonts_src.is_dir():
        shutil.copytree(fonts_src, BUILD_DIR / "fonts")

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
