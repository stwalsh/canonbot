#!/usr/bin/env python3
"""
parse_housman.py — Parse two Gutenberg plaintext files of A. E. Housman's poetry
into intermediate JSON.

Input files:
  - corpus/raw/gutenberg/housman_shropshire.txt  (#5720, A Shropshire Lad)
  - corpus/raw/gutenberg/housman_last.txt         (#7848, Last Poems)

Shropshire Lad format:
  - Gutenberg boilerplate, then Braithwaite introduction (skip)
  - Poems numbered with indented Roman numerals on their own line
  - Some poems have a title (ALL CAPS) on the next non-blank line; some don't
  - Untitled poems: use first verse line as title

Last Poems format:
  - Gutenberg boilerplate, then short author's preface (skip)
  - Epigraph poem "We'll to the woods no more" before poem I
  - Poems numbered as "I. THE WEST" or "II." (Roman numeral + period, optional title)
  - Same stanza/blank-line structure

Usage:
    python scripts/parse_housman.py
"""

import json
import re
from pathlib import Path

SHROPSHIRE_PATH = Path("corpus/raw/gutenberg/housman_shropshire.txt")
LAST_PATH = Path("corpus/raw/gutenberg/housman_last.txt")
OUT_PATH = Path("corpus/intermediate/GUT_housman-poems.json")

# Roman numeral validation
ROMAN_RE = re.compile(r"^[IVXLC]+$")


def _smart_title(s: str) -> str:
    """Title-case that handles apostrophes properly.

    Python's str.title() capitalizes after apostrophes, giving
    "Carpenter'S" instead of "Carpenter's". Fix that.
    """
    titled = s.title()
    # Fix "'S " and "'S$" → "'s " and "'s"
    titled = re.sub(r"'S\b", "'s", titled)
    return titled


def _roman_to_int(s: str) -> int:
    """Convert a Roman numeral string to integer."""
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = 0
    prev = 0
    for ch in reversed(s):
        v = vals[ch]
        if v < prev:
            total -= v
        else:
            total += v
        prev = v
    return total


def _strip_gutenberg(lines: list[str]) -> list[str]:
    """Return only the lines between *** START and *** END markers."""
    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        if "*** START OF" in line:
            start = i + 1
        if "*** END OF" in line:
            end = i
            break
    if start is None:
        print("  WARNING: no Gutenberg START marker found")
        start = 0
    return lines[start:end]


def _lines_to_stanzas(verse_lines: list[str]) -> list[dict]:
    """Split verse lines into stanzas on blank lines. Strip leading whitespace."""
    stanzas = []
    current = []
    for line in verse_lines:
        if not line.strip():
            if current:
                stanzas.append({"stanza_num": "", "lines": current})
                current = []
        else:
            cleaned = line.strip()
            # Strip Gutenberg italic markers (underscores at start/end of line)
            cleaned = re.sub(r"^_\s+", "", cleaned)
            cleaned = re.sub(r"\s+_$", "", cleaned)
            cleaned = cleaned.strip()
            if cleaned:
                current.append(cleaned)
    if current:
        stanzas.append({"stanza_num": "", "lines": current})
    return stanzas


def parse_shropshire(raw_lines: list[str]) -> list[dict]:
    """Parse A Shropshire Lad into poem dicts."""
    lines = _strip_gutenberg(raw_lines)

    # Skip the introduction: find "A SHROPSHIRE LAD" heading that precedes poem I.
    # The second occurrence (after the title page one) immediately precedes the poems.
    # Then find the first Roman numeral line after it.
    poems_start = None
    seen_heading = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "A SHROPSHIRE LAD":
            seen_heading += 1
            if seen_heading == 2:
                # Poems start after this heading
                poems_start = i + 1
                break

    if poems_start is None:
        # Fallback: find first indented roman numeral
        for i, line in enumerate(lines):
            content = line.rstrip().lstrip()
            if ROMAN_RE.match(content) and _roman_to_int(content) == 1:
                poems_start = i
                break

    if poems_start is None:
        print("  ERROR: Could not find start of Shropshire Lad poems")
        return []

    print(f"  Shropshire Lad: poems start at content line {poems_start}")

    # Split into poem blocks by indented Roman numerals on their own line.
    # Pattern: line that is only whitespace + Roman numeral
    poem_blocks = []
    current_num = None
    current_lines = []

    for i in range(poems_start, len(lines)):
        line = lines[i].rstrip("\n")
        content = line.strip()

        # Check if this line is a standalone Roman numeral
        if content and ROMAN_RE.match(content) and len(content) <= 7:
            num = _roman_to_int(content)
            # Sanity: should be sequential-ish (within range)
            if 1 <= num <= 70:
                if current_num is not None:
                    poem_blocks.append((current_num, current_lines))
                current_num = num
                current_lines = []
                continue

        current_lines.append(line)

    if current_num is not None:
        poem_blocks.append((current_num, current_lines))

    print(f"  Shropshire Lad: found {len(poem_blocks)} poem blocks")

    # Parse each block: check for title, then extract stanzas
    poems = []
    for num, block_lines in poem_blocks:
        # Strip leading/trailing blank lines
        while block_lines and not block_lines[0].strip():
            block_lines = block_lines[1:]
        while block_lines and not block_lines[-1].strip():
            block_lines = block_lines[:-1]

        if not block_lines:
            continue

        # Check if first non-blank line is a title
        # Titles in Shropshire Lad are ALL CAPS or numeric like "1887"
        first = block_lines[0].strip()
        title = None
        verse_start = 0

        # Title patterns: ALL CAPS (with possible parenthetical), or "1887"
        if first and (
            (first.replace(" ", "").replace("(", "").replace(")", "")
             .replace("'", "").replace("-", "").replace("1", "").replace("8", "")
             .replace("7", "").isalpha()
             and first == first.upper()
             and len(first) < 60)
            or re.match(r"^\d{4}$", first)
        ):
            title = first
            # Strip "(1)" etc. from titles like "BREDON HILL (1)"
            title = re.sub(r"\s*\(\d+\)\s*$", "", title)
            # Title-case for output
            title = _smart_title(title)
            verse_start = 1
            # Skip blank line after title
            while verse_start < len(block_lines) and not block_lines[verse_start].strip():
                verse_start += 1

        verse_lines = block_lines[verse_start:]
        stanzas = _lines_to_stanzas(verse_lines)

        if not stanzas:
            continue

        # If no explicit title, use first verse line (strip italic markers)
        if title is None:
            first_line = stanzas[0]["lines"][0]
            # Strip Gutenberg italic markers like "_ Clunton ..."
            first_line = re.sub(r"^_\s*", "", first_line)
            first_line = re.sub(r"\s*_\s*$", "", first_line)
            title = first_line

        n_lines = sum(len(s["lines"]) for s in stanzas)
        poems.append({"title": title, "stanzas": stanzas})
        print(f"    {num:3d}. {title:50s} {len(stanzas):2d} st  {n_lines:3d} ln")

    return poems


def parse_last_poems(raw_lines: list[str]) -> list[dict]:
    """Parse Last Poems into poem dicts."""
    lines = _strip_gutenberg(raw_lines)

    # Find where numbered poems begin (skip preface and epigraph).
    # The epigraph "We'll to the woods no more" comes before "I. THE WEST".
    # We want to capture the epigraph as a separate poem.

    # First, skip the "Produced by" line and find the preface/epigraph region.
    # Look for "I. " pattern to find start of numbered poems.
    numbered_start = None
    numeral_re = re.compile(r"^([IVXLC]+)\.\s*(.*?)\s*$")

    for i, line in enumerate(lines):
        m = numeral_re.match(line.strip())
        if m and _roman_to_int(m.group(1)) == 1:
            numbered_start = i
            break

    if numbered_start is None:
        print("  ERROR: Could not find start of Last Poems numbered poems")
        return []

    print(f"  Last Poems: numbered poems start at content line {numbered_start}")

    poems = []

    # Extract epigraph: verse lines between the preface and poem I.
    # Look backwards from numbered_start for verse lines.
    epigraph_lines = []
    for i in range(numbered_start - 1, -1, -1):
        content = lines[i].strip()
        if not content:
            if epigraph_lines:
                break  # blank line before epigraph block = done
            continue
        # Stop if we hit prose (the preface)
        if content.startswith("September") or len(content) > 80:
            break
        epigraph_lines.insert(0, lines[i])

    if epigraph_lines:
        stanzas = _lines_to_stanzas(epigraph_lines)
        if stanzas:
            title = stanzas[0]["lines"][0]
            n_lines = sum(len(s["lines"]) for s in stanzas)
            poems.append({"title": title, "stanzas": stanzas})
            print(f"    [ep] {title:50s} {len(stanzas):2d} st  {n_lines:3d} ln")

    # Parse numbered poems
    poem_blocks = []
    current_num = None
    current_title = None
    current_lines = []

    for i in range(numbered_start, len(lines)):
        line = lines[i].rstrip("\n")
        content = line.strip()

        m = numeral_re.match(content)
        if m:
            num = _roman_to_int(m.group(1))
            if 1 <= num <= 50:
                if current_num is not None:
                    poem_blocks.append((current_num, current_title, current_lines))
                current_num = num
                current_title = m.group(2).strip() if m.group(2).strip() else None
                current_lines = []
                continue

        current_lines.append(line)

    if current_num is not None:
        poem_blocks.append((current_num, current_title, current_lines))

    print(f"  Last Poems: found {len(poem_blocks)} numbered poem blocks")

    for num, explicit_title, block_lines in poem_blocks:
        # Strip leading/trailing blank lines
        while block_lines and not block_lines[0].strip():
            block_lines = block_lines[1:]
        while block_lines and not block_lines[-1].strip():
            block_lines = block_lines[:-1]

        if not block_lines:
            continue

        # Check if first non-blank line is a separate ALL-CAPS title
        # (e.g. poem XXXIV where "THE FIRST OF MAY" is on its own line)
        if not explicit_title and block_lines:
            first = block_lines[0].strip()
            # ALL CAPS, short, alphabetic (with spaces) = likely a title
            if (first and first == first.upper()
                    and len(first) < 50
                    and re.match(r"^[A-Z\s']+$", first)):
                explicit_title = first
                block_lines = block_lines[1:]
                # Strip blank lines after title
                while block_lines and not block_lines[0].strip():
                    block_lines = block_lines[1:]

        stanzas = _lines_to_stanzas(block_lines)
        if not stanzas:
            continue

        # Use explicit title if given, otherwise first line
        if explicit_title:
            title = _smart_title(explicit_title)
        else:
            title = stanzas[0]["lines"][0]

        n_lines = sum(len(s["lines"]) for s in stanzas)
        poems.append({"title": title, "stanzas": stanzas})
        print(f"    {num:3d}. {title:50s} {len(stanzas):2d} st  {n_lines:3d} ln")

    return poems


def parse():
    print("Parsing A. E. Housman poems...")
    print()

    # Parse both collections
    print(f"Reading {SHROPSHIRE_PATH}")
    with open(SHROPSHIRE_PATH, encoding="utf-8") as f:
        shropshire_lines = f.readlines()
    shropshire_poems = parse_shropshire(shropshire_lines)

    print()
    print(f"Reading {LAST_PATH}")
    with open(LAST_PATH, encoding="utf-8") as f:
        last_lines = f.readlines()
    last_poems = parse_last_poems(last_lines)

    all_poems = shropshire_poems + last_poems
    total_lines = sum(
        sum(len(s["lines"]) for s in p["stanzas"])
        for p in all_poems
    )

    print()
    print(f"Total: {len(all_poems)} poems, {total_lines} lines")
    print(f"  Shropshire Lad: {len(shropshire_poems)} poems")
    print(f"  Last Poems: {len(last_poems)} poems")

    output = {
        "tcp_id": "",
        "gutenberg_id": "5720+7848",
        "author": "A. E. Housman",
        "title": "A Shropshire Lad and Last Poems",
        "date": "1896",
        "source": "Gutenberg",
        "poems": all_poems,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Written: {OUT_PATH}")


if __name__ == "__main__":
    parse()
