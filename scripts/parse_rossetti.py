#!/usr/bin/env python3
"""
parse_rossetti.py — Parse Gutenberg #16950 (Goblin Market, The Prince's Progress,
and Other Poems) into intermediate JSON.

Plain text format:
  - ALL CAPS titles for poems
  - Blank lines between stanzas
  - Inline line numbers every 10th line (right-justified, e.g. "  10", "  540")
  - Two sections: "GOBLIN MARKET, AND OTHER POEMS, 1862" and
    "THE PRINCE'S PROGRESS, AND OTHER POEMS, 1866"

Usage:
    python scripts/parse_rossetti.py
"""

import json
import re
from pathlib import Path

RAW_PATH = Path("corpus/raw/gutenberg/rossetti_goblin_market.txt")
OUT_PATH = Path("corpus/intermediate/GUT16950_christina-rossetti.json")

# Section headers to skip (not poem titles)
SKIP_TITLES = {
    "GOBLIN MARKET, AND OTHER POEMS, 1862",
    "THE PRINCE'S PROGRESS, AND OTHER POEMS, 1866",
    "DEVOTIONAL PIECES",
    "CONTENTS",
    "SONNETS ARE FULL OF LOVE",
    "MISCELLANEOUS POEMS, 1848-69",
}

# Subtitle/form labels that appear alone on a line after the title — not verse
FORM_LABELS = {"Sonnet", "Song", "Dirge"}


def _strip_line_numbers(line: str) -> str:
    """Remove right-justified Gutenberg line numbers (e.g. trailing '  540')."""
    return re.sub(r"\s+\d+\s*$", "", line)


def _is_title(line: str) -> bool:
    """Detect ALL CAPS poem titles. Must be mostly uppercase letters."""
    stripped = line.strip()
    if not stripped or len(stripped) < 3:
        return False
    # Skip lines that are just numbers or Roman numerals
    if re.match(r"^[IVXLC]+\.?\s*$", stripped):
        return False
    # Must be predominantly uppercase letters (allow punctuation, spaces)
    alpha = [c for c in stripped if c.isalpha()]
    if not alpha:
        return False
    upper_ratio = sum(1 for c in alpha if c.isupper()) / len(alpha)
    return upper_ratio > 0.85 and len(alpha) >= 3


def parse():
    with open(RAW_PATH, encoding="utf-8") as f:
        raw = f.read()

    # Strip Gutenberg header/footer
    start = raw.find("GOBLIN MARKET, AND OTHER POEMS, 1862")
    end = raw.find("*** END OF THE PROJECT GUTENBERG EBOOK")
    if start == -1 or end == -1:
        print("ERROR: Could not find text boundaries")
        return
    # Back up to find the section header that precedes the first poem
    text = raw[start:end]

    lines = text.split("\n")
    poems = []
    current_title = None
    current_lines = []

    def flush_poem():
        nonlocal current_title, current_lines
        if current_title and current_title not in SKIP_TITLES:
            # Split into stanzas on blank lines
            stanzas = []
            stanza = []
            for line in current_lines:
                cleaned = _strip_line_numbers(line).rstrip()
                if not cleaned:
                    if stanza:
                        stanzas.append(stanza)
                        stanza = []
                elif cleaned.strip() in FORM_LABELS:
                    continue  # Skip "Sonnet", "Song" etc. form labels
                elif re.match(r"^\(.*\d{4}\)$", cleaned.strip()):
                    continue  # Skip publication notes like "(Athenaeum, 1848)"
                else:
                    stanza.append(cleaned)
            if stanza:
                stanzas.append(stanza)

            if stanzas:
                total = sum(len(s) for s in stanzas)
                poems.append({
                    "title": _clean_title(current_title),
                    "stanzas": [
                        {"stanza_num": "", "lines": s}
                        for s in stanzas
                    ],
                })
        current_title = None
        current_lines = []

    for line in lines:
        stripped = line.strip()

        if _is_title(stripped):
            flush_poem()
            current_title = stripped
        elif current_title:
            current_lines.append(line)

    flush_poem()

    total_lines = sum(
        sum(len(s["lines"]) for s in p["stanzas"])
        for p in poems
    )
    print(f"  {len(poems)} poems, {total_lines} lines")

    output = {
        "tcp_id": "",
        "gutenberg_id": "16950",
        "author": "Christina Rossetti",
        "title": "Goblin Market, The Prince's Progress, and Other Poems",
        "date": "1862",
        "source": "Gutenberg",
        "poems": poems,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"  Written: {OUT_PATH}")

    # Show first 10 poem titles
    for p in poems[:10]:
        lines = sum(len(s["lines"]) for s in p["stanzas"])
        print(f"    {p['title'][:55]:55s} {lines:4d} lines")
    if len(poems) > 10:
        print(f"    ... and {len(poems) - 10} more")


def _clean_title(title: str) -> str:
    """Convert ALL CAPS title to title case, with cleanup."""
    # Handle subtitles after comma or colon
    title = title.strip()
    # Special cases
    if title == "GOBLIN MARKET":
        return "Goblin Market"
    if title == "THE PRINCE'S PROGRESS":
        return "The Prince's Progress"
    # General: convert to title case
    result = title.title()
    # Fix common title-case artifacts
    result = result.replace("'S ", "'s ")
    result = result.replace("'T ", "'t ")
    # Fix prepositions/articles mid-title
    for word in ["A", "An", "The", "Of", "In", "On", "At", "To", "For", "And", "But", "Or", "Nor", "With", "From", "By"]:
        result = result.replace(f" {word} ", f" {word.lower()} ")
    # First word always capitalized
    if result:
        result = result[0].upper() + result[1:]
    return result


if __name__ == "__main__":
    parse()
