#!/usr/bin/env python3
"""
parse_prelude.py — Parse OTA plaintext Prelude into intermediate JSON.

Source: Oxford Text Archive #3138 (1850 text, despite the catalogue saying 1805).
Format: Plain text, book headings as "BOOK FIRST\nSUBTITLE", blank verse
with occasional verse-paragraph breaks (blank lines).

Usage:
    python scripts/parse_prelude.py
"""

import json
import re
from pathlib import Path

RAW_PATH = Path("corpus/raw/ota/wordsworth_prelude_1850.txt")
OUT_PATH = Path("corpus/intermediate/OTA_wordsworth-prelude.json")

# Book ordinals
_ORDINALS = {
    "FIRST": "I", "SECOND": "II", "THIRD": "III", "FOURTH": "IV",
    "FIFTH": "V", "SIXTH": "VI", "SEVENTH": "VII", "EIGHTH": "VIII",
    "NINTH": "IX", "TENTH": "X", "ELEVENTH": "XI", "TWELFTH": "XII",
    "THIRTEENTH": "XIII", "FOURTEENTH": "XIV",
}

_BOOK_RE = re.compile(r"^BOOK\s+(\w+)\s*$")


def parse():
    with open(RAW_PATH) as f:
        lines = f.readlines()

    # Skip the advertisement/preface — find first "BOOK FIRST"
    start = None
    for i, line in enumerate(lines):
        if _BOOK_RE.match(line.strip()):
            start = i
            break

    if start is None:
        print("ERROR: Could not find BOOK FIRST")
        return

    # Parse into books
    books = []
    current_book = None
    current_subtitle = None
    current_lines = []

    for i in range(start, len(lines)):
        line = lines[i].rstrip()
        stripped = line.strip()

        m = _BOOK_RE.match(stripped)
        if m:
            # Flush previous book
            if current_book:
                books.append(_make_book(current_book, current_subtitle, current_lines))
                current_lines = []
            ordinal = m.group(1).upper()
            roman = _ORDINALS.get(ordinal, ordinal)
            current_book = roman
            current_subtitle = None
            continue

        # Line immediately after "BOOK X" is the subtitle
        if current_book and current_subtitle is None and not current_lines:
            if stripped and not stripped[0].isdigit():
                current_subtitle = stripped
                continue

        # Date line at end (e.g. "1799-1805.")
        if re.match(r"^\d{4}[–-]\d{4}", stripped):
            continue

        current_lines.append(line)

    # Flush last book
    if current_book:
        books.append(_make_book(current_book, current_subtitle, current_lines))

    total_stanzas = sum(len(b["stanzas"]) for b in books)
    total_lines = sum(sum(len(s["lines"]) for s in b["stanzas"]) for b in books)
    print(f"  {len(books)} books, {total_stanzas} verse-paragraphs, {total_lines} lines")

    for b in books:
        n = sum(len(s["lines"]) for s in b["stanzas"])
        print(f"    {b['title']:60s} {n:5d} lines")

    output = {
        "tcp_id": "",
        "gutenberg_id": "",
        "author": "William Wordsworth",
        "title": "The Prelude (1850)",
        "date": "1850",
        "source": "Oxford Text Archive #3138",
        "poems": books,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"  Written: {OUT_PATH}")


def _make_book(roman: str, subtitle: str, lines: list[str]) -> dict:
    """Build a poem dict for one book of The Prelude."""
    title = f"The Prelude: Book {roman}"
    if subtitle:
        title += f" — {subtitle.title()}"

    # Split into verse-paragraphs (stanzas) on blank lines
    stanzas = []
    current = []
    for line in lines:
        if not line.strip():
            if current:
                stanzas.append({"stanza_num": "", "lines": current})
                current = []
        else:
            current.append(line)
    if current:
        stanzas.append({"stanza_num": "", "lines": current})

    return {
        "title": title,
        "stanzas": stanzas,
    }


if __name__ == "__main__":
    parse()
