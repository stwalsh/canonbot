#!/usr/bin/env python3
"""
parse_erdman.py — Fetch and parse the Erdman edition of Blake from the
Blake Archive API into two outputs:

1. Canonbot intermediate JSON (lyric/shorter works) → corpus/intermediate/
2. Complete Erdman store (all works) → corpus/blake_erdman/

API: https://erdman.blakearchive.org/api/pages

Usage:
    python scripts/parse_erdman.py              # Fetch from API, parse both outputs
    python scripts/parse_erdman.py --cached     # Use cached /tmp/erdman_pages.json
"""

import json
import os
import re
import sys
from html import unescape
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_URL = "https://erdman.blakearchive.org/api/pages"
CACHE_PATH = "/tmp/erdman_pages.json"

INTERMEDIATE_DIR = Path("corpus/intermediate")
COMPLETE_DIR = Path("corpus/blake_erdman")

POET = "William Blake"
SOURCE = "Erdman"

# Page ranges for canonbot (lyric/shorter works)
# Each entry: (label, start_page, end_page, work_title)
CANONBOT_SECTIONS = [
    ("All Religions are One", 1, 1, "All Religions are One"),
    ("No Natural Religion", 2, 2, "There is No Natural Religion"),
    ("Book of Thel", 3, 6, "The Book of Thel"),
    ("Songs of Innocence and Experience", 7, 31, "Songs of Innocence and of Experience"),
    ("Gates of Paradise (children)", 32, 32, "For Children: The Gates of Paradise"),
    ("Marriage of Heaven and Hell", 33, 44, "The Marriage of Heaven and Hell"),
    ("Visions of the Daughters of Albion", 45, 50, "Visions of the Daughters of Albion"),
    ("America a Prophecy", 51, 59, "America a Prophecy"),
    ("Europe a Prophecy", 60, 66, "Europe a Prophecy"),
    ("Song of Los", 67, 69, "The Song of Los"),
    ("Book of Urizen", 70, 83, "The Book of Urizen"),
    ("Book of Ahania", 84, 89, "The Book of Ahania"),
    ("Book of Los", 90, 94, "The Book of Los"),
    ("Gates of Paradise (sexes)", 259, 268, "For The Sexes: The Gates of Paradise"),
    ("On Homers Poetry / On Virgil", 269, 270, "On Homers Poetry & On Virgil"),
    ("Ghost of Abel", 270, 271, "The Ghost of Abel"),
    ("Laocoon", 273, 275, "The Laocoon"),
    ("Notebook poems & Pickering MS", 466, 498, "Notebook Poems & Pickering Manuscript"),
    ("Satirical & occasional verse", 499, 517, "Satirical & Occasional Verse"),
    ("Everlasting Gospel", 518, 525, "The Everlasting Gospel"),
]

# All works for the complete store (covers the full edition)
COMPLETE_SECTIONS = [
    ("All Religions are One", 1, 1, "All Religions are One"),
    ("No Natural Religion", 2, 2, "There is No Natural Religion"),
    ("Book of Thel", 3, 6, "The Book of Thel"),
    ("Songs of Innocence and Experience", 7, 31, "Songs of Innocence and of Experience"),
    ("Gates of Paradise (children)", 32, 32, "For Children: The Gates of Paradise"),
    ("Marriage of Heaven and Hell", 33, 44, "The Marriage of Heaven and Hell"),
    ("Visions of the Daughters of Albion", 45, 50, "Visions of the Daughters of Albion"),
    ("America a Prophecy", 51, 59, "America a Prophecy"),
    ("Europe a Prophecy", 60, 66, "Europe a Prophecy"),
    ("Song of Los", 67, 69, "The Song of Los"),
    ("Book of Urizen", 70, 83, "The Book of Urizen"),
    ("Book of Ahania", 84, 89, "The Book of Ahania"),
    ("Book of Los", 90, 94, "The Book of Los"),
    ("Milton", 95, 143, "Milton a Poem"),
    ("Jerusalem", 144, 258, "Jerusalem"),
    ("Gates of Paradise (sexes)", 259, 268, "For The Sexes: The Gates of Paradise"),
    ("On Homers Poetry / On Virgil", 269, 270, "On Homers Poetry & On Virgil"),
    ("Ghost of Abel", 270, 271, "The Ghost of Abel"),
    ("Laocoon", 273, 275, "The Laocoon"),
    ("Tiriel", 276, 284, "Tiriel"),
    ("French Revolution", 285, 299, "The French Revolution"),
    ("Four Zoas", 300, 407, "The Four Zoas"),
    ("Miscellaneous Poems", 408, 445, "Miscellaneous Poems"),
    ("An Island in the Moon", 449, 465, "An Island in the Moon"),
    ("Notebook poems & Pickering MS", 466, 498, "Notebook Poems & Pickering Manuscript"),
    ("Satirical & occasional verse", 499, 517, "Satirical & Occasional Verse"),
    ("Everlasting Gospel", 518, 525, "The Everlasting Gospel"),
    ("Descriptive Catalogue", 526, 551, "A Descriptive Catalogue"),
    ("Vision of Last Judgment", 552, 566, "A Vision of The Last Judgment"),
    ("Annotations to Lavater", 583, 600, "Annotations to Lavater"),
    ("Annotations to Swedenborg", 601, 608, "Annotations to Swedenborg"),
    ("Annotations to Watson", 611, 619, "Annotations to Watson"),
    ("Annotations to Bacon", 620, 632, "Annotations to Bacon"),
    ("Annotations to Reynolds", 635, 661, "Annotations to Reynolds"),
    ("Annotations to Wordsworth", 665, 667, "Annotations to Wordsworth"),
    ("Annotations to Thornton", 667, 669, "Annotations to Thornton"),
    ("Letters", 699, 784, "Letters"),
]


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def fetch_pages(use_cache: bool = False) -> list[dict]:
    """Fetch all pages from the Erdman API."""
    if use_cache and os.path.exists(CACHE_PATH):
        print(f"Loading cached pages from {CACHE_PATH}")
        with open(CACHE_PATH) as f:
            return json.load(f)

    print(f"Fetching pages from {API_URL}...")
    resp = requests.get(API_URL, timeout=60)
    resp.raise_for_status()
    pages = resp.json()

    with open(CACHE_PATH, "w") as f:
        json.dump(pages, f)
    print(f"Cached {len(pages)} pages to {CACHE_PATH}")

    return pages


# ---------------------------------------------------------------------------
# HTML → text extraction
# ---------------------------------------------------------------------------


def strip_notes(html: str) -> str:
    """Remove Erdman's editorial notes (in <note> tags)."""
    return re.sub(r"<note\b[^>]*>.*?</note>", "", html, flags=re.DOTALL)


def strip_editorial(html: str) -> str:
    """Remove editorial apparatus — page refs, Erdman's annotations."""
    # Remove <biblstruct> blocks (edition metadata)
    html = re.sub(r"<biblstruct\b[^>]*>.*?</biblstruct>", "", html, flags=re.DOTALL)
    # Remove sr-only spans (structural IDs)
    html = re.sub(r'<span class="sr-only"[^>]*>[^<]*</span>', "", html)
    # Remove note-reference spans (contain "t" or "d" markers)
    html = re.sub(r'<span\s+class="note-reference"[^>]*>.*?</span>', "", html, flags=re.DOTALL)
    # Remove images
    html = re.sub(r"<img[^>]*>", "", html)
    # Remove cover-image divs
    html = re.sub(r'<div class="cover-image">.*?</div>', "", html, flags=re.DOTALL)
    return html


def html_to_lines(html: str) -> list[str]:
    """Convert HTML content to lines of text (fallback for prose).

    Handles <br>, <p>, <div>, <l> (verse line) tags.
    """
    # Normalize line-break-like tags to newlines
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</p>|</div>|</l>", "\n", text)
    text = re.sub(r"<p[^>]*>|<div[^>]*>|<l[^>]*>", "", text)

    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)

    # Clean up whitespace
    lines = []
    for line in text.split("\n"):
        line = line.rstrip()
        line = line.replace("\xa0", " ")
        # Remove line numbers that appear as bare numbers at end of line
        line = re.sub(r"\s+\d+\s*$", "", line)
        if line.strip():
            lines.append(line)

    return lines


def _extract_tei_stanzas(html: str) -> list[list[str]]:
    """Extract stanzas from TEI markup (tei-linegroup / tei-line).

    Returns list of stanzas, each a list of line strings.
    """
    stanzas = []
    for lg_match in re.finditer(
        r'<ol\s+class="tei-linegroup"[^>]*>(.*?)</ol>', html, re.DOTALL
    ):
        lines = []
        for line_match in re.finditer(
            r'<li\s+class="tei-line[^"]*"[^>]*>(.*?)</li>', lg_match.group(1), re.DOTALL
        ):
            # Extract text from tei-line-text span, ignore tei-line-note and tei-line-number
            line_html = line_match.group(1)
            # Remove line number spans
            line_html = re.sub(r'<span\s+class="tei-line-number"[^>]*>.*?</span>', "", line_html)
            # Remove note spans
            line_html = re.sub(r'<span\s+class="tei-line-note"[^/]*/>', "", line_html)
            # Strip all remaining tags
            text = re.sub(r"<[^>]+>", "", line_html)
            text = unescape(text).rstrip()
            text = text.replace("\xa0", " ")
            if text.strip():
                lines.append(text)
        if lines:
            stanzas.append(lines)
    return stanzas


def _extract_prose_lines(html: str) -> list[str]:
    """Extract prose lines from <p> tags (for non-verse sections)."""
    lines = []
    for p_match in re.finditer(r"<p[^>]*>(.*?)</p>", html, re.DOTALL):
        text = re.sub(r"<[^>]+>", "", p_match.group(1))
        text = unescape(text).strip()
        text = text.replace("\xa0", " ")
        if text and text not in ("t", "d"):
            lines.append(text)
    return lines


def extract_poems_from_pages(pages: list[dict], start: int, end: int) -> list[dict]:
    """Extract poems from a range of pages.

    Splits on h1-h4 headings. Uses TEI markup for verse lines when available,
    falls back to prose extraction for <p> content.

    Returns list of {title, stanzas: [{stanza_num, lines, gaps}]}
    """
    poems = []
    current_title = None
    current_stanzas = []  # list of (lines_list) — one per stanza
    current_prose = []    # prose lines (from <p> outside TEI markup)

    def _flush():
        """Save current poem if it has content."""
        nonlocal current_title, current_stanzas, current_prose
        if not current_title:
            return
        # Build stanzas from TEI stanzas + any prose
        stanzas = []
        for lines in current_stanzas:
            stanzas.append({"stanza_num": "", "lines": lines, "gaps": []})
        if current_prose:
            stanzas.append({"stanza_num": "", "lines": current_prose, "gaps": []})
        if stanzas:
            title = current_title.strip()
            title = re.sub(r"\s+[td]\s*$", "", title)
            poems.append({"title": title, "stanzas": stanzas})
        current_title = None
        current_stanzas = []
        current_prose = []

    for page in pages:
        pid = page["page_id"]

        # Parse page_id as int where possible
        try:
            page_num = int(pid)
        except ValueError:
            m = re.match(r"(\d+)", pid)
            if m:
                page_num = int(m.group(1))
            else:
                continue

        if page_num < start or page_num > end:
            continue

        contents = page.get("contents", "")
        if not contents:
            continue

        # Strip editorial apparatus
        contents = strip_notes(contents)
        contents = strip_editorial(contents)

        # Split on any heading level h1-h4
        parts = re.split(
            r"(<h[1-4][^>]*>.*?</h[1-4]>)", contents, flags=re.DOTALL
        )

        for part in parts:
            heading_match = re.match(
                r"<h[1-4][^>]*>(.*?)</h[1-4]>", part, re.DOTALL
            )
            if heading_match:
                _flush()

                title = re.sub(r"<[^>]+>", "", heading_match.group(1))
                title = unescape(title).strip()
                title = re.sub(r"\s+", " ", title)

                # Skip purely structural/empty titles
                if title in ("t", "d") or not title:
                    continue

                current_title = title
                current_stanzas = []
                current_prose = []
            else:
                # Extract TEI stanzas if present
                tei_stanzas = _extract_tei_stanzas(part)
                if tei_stanzas:
                    current_stanzas.extend(tei_stanzas)

                # Also grab prose <p> content not inside TEI markup
                # Strip out the TEI blocks first to avoid double-counting
                prose_html = re.sub(
                    r'<ol\s+class="tei-linegroup"[^>]*>.*?</ol>', "", part, flags=re.DOTALL
                )
                prose_lines = _extract_prose_lines(prose_html)
                if prose_lines:
                    current_prose.extend(prose_lines)

    _flush()
    return poems




# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_canonbot_intermediate(pages: list[dict]):
    """Write canonbot intermediate JSON for selected Blake works."""
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)

    all_poems = []
    for label, start, end, work_title in CANONBOT_SECTIONS:
        poems = extract_poems_from_pages(pages, start, end)
        print(f"  {label}: {len(poems)} poems (pp. {start}-{end})")
        for poem in poems:
            poem["_work"] = work_title
        all_poems.extend(poems)

    # Group by work
    by_work = {}
    for poem in all_poems:
        work = poem.pop("_work")
        by_work.setdefault(work, []).append(poem)

    total = 0
    for work_title, poems in by_work.items():
        slug = re.sub(r"[^a-z0-9]+", "-", work_title.lower()).strip("-")[:50]
        out = {
            "tcp_id": "",
            "gutenberg_id": "",
            "erdman_source": "Blake Archive Erdman Edition",
            "author": POET,
            "title": work_title,
            "date": "1794",  # Central Blake date
            "source": SOURCE,
            "poems": poems,
        }
        filename = f"ERDMAN_{slug}.json"
        outpath = INTERMEDIATE_DIR / filename
        with open(outpath, "w") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        total += len(poems)
        print(f"    → {filename}: {len(poems)} poems")

    print(f"\n  Canonbot total: {total} poems across {len(by_work)} works")


def write_complete_store(pages: list[dict]):
    """Write complete Erdman Blake to separate store."""
    COMPLETE_DIR.mkdir(parents=True, exist_ok=True)

    total = 0
    for label, start, end, work_title in COMPLETE_SECTIONS:
        poems = extract_poems_from_pages(pages, start, end)
        if not poems:
            print(f"  {label}: 0 poems (pp. {start}-{end}) — skipping")
            continue

        slug = re.sub(r"[^a-z0-9]+", "-", work_title.lower()).strip("-")[:50]
        out = {
            "author": POET,
            "title": work_title,
            "date": "1794",
            "source": "Erdman (Blake Archive)",
            "page_range": f"{start}-{end}",
            "poems": poems,
        }
        filename = f"{slug}.json"
        outpath = COMPLETE_DIR / filename
        with open(outpath, "w") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)

        n_lines = sum(
            sum(len(s["lines"]) for s in p["stanzas"])
            for p in poems
        )
        total += len(poems)
        print(f"  {filename}: {len(poems)} poems, {n_lines} lines")

    print(f"\n  Complete store total: {total} poems")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    use_cache = "--cached" in sys.argv

    pages = fetch_pages(use_cache=use_cache)
    print(f"Total pages: {len(pages)}\n")

    print("=== Canonbot intermediate (lyric/shorter works) ===")
    write_canonbot_intermediate(pages)

    print("\n=== Complete Erdman store ===")
    write_complete_store(pages)

    print("\nDone.")
    print("  Canonbot: run chunk_corpus.py + ingest_to_chroma.py to embed")
    print(f"  Complete store: {COMPLETE_DIR}/")


if __name__ == "__main__":
    main()
