#!/usr/bin/env python3
"""
parse_swinburne.py — Parse OTA #1243 Swinburne selected poems into intermediate JSON.

The OTA plaintext is lightly formatted but has quirks:
  - Heavy indentation (varies per poem)
  - Hertha and Garden of Proserpine: blank lines between every short line
  - Line-wrapped long lines (Nephelidia)
  - Inconsistent title formatting (title-case vs ALL CAPS)
  - Only 9 poems, so we hardcode boundaries

Usage:
    python scripts/parse_swinburne.py
"""

import json
import re
from pathlib import Path

RAW_PATH = Path("corpus/raw/ota/swinburne_selected.txt")
OUT_PATH = Path("corpus/intermediate/OTA_swinburne-selected.json")

# Poem boundaries: (start_line_1indexed, title, date)
# Identified by manual inspection of the file
POEMS = [
    (3, "Hymn to Proserpine", "1866"),
    (129, "Laus Veneris", "1866"),
    (675, "Faustine", "1866"),
    (889, "The Triumph of Time", "1866"),
    (1337, "Hertha", "1871"),
    (1844, "The Garden of Proserpine", "1866"),
    (2072, "Ave Atque Vale", "1868"),
    (2558, "Itylus", "1866"),
    (2704, "Nephelidia", "1880"),
]


def _dejoin_lines(lines: list[str]) -> list[str]:
    """Rejoin lines that were wrapped at column ~75.

    A continuation line is indented less or similarly to the previous line
    and starts with a lowercase letter. This is imperfect but catches the
    Nephelidia long-line wrapping.
    """
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append("")
            continue

        # Check if this is a continuation of the previous line
        if result and result[-1] and stripped and stripped[0].islower():
            # Probably a continuation
            result[-1] = result[-1].rstrip() + " " + stripped
        else:
            result.append(stripped)
    return result


def _parse_poem_block(lines: list[str], title: str) -> list[dict]:
    """Parse a block of lines into stanzas.

    Handles:
    - Normal stanzas separated by blank lines
    - Hertha-style: short indented lines with blanks between each pair,
      plus a long closing line — each stanza is 4 short + 1 long
    - Ave Atque Vale: Roman numeral section markers (I–XVIII) as stanza numbers
    """
    # Strip leading/trailing blank lines
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]

    # Skip subtitle/epigraph lines before first verse
    # (e.g., Hymn to Proserpine has "(After the Proclamation...)" and "Vicisti, Galilaee")
    verse_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # Subtitle in parens, or short Latin epigraph, or blank
        if stripped.startswith("(") or (len(stripped) < 40 and not any(c in stripped for c in ".;:!?")):
            verse_start = i + 1
            continue
        break

    lines = lines[verse_start:]

    # Rejoin wrapped lines
    lines = _dejoin_lines(lines)

    # Detect if this is Ave Atque Vale (has Roman numeral sections)
    has_roman = any(re.match(r"^(I{1,3}|IV|V|VI{0,3}|IX|X{1,3}|XI{1,3}|XIV|XV|XVI{0,3}|XVIII?)$", l.strip()) for l in lines if l.strip())

    if has_roman:
        return _parse_roman_sections(lines)

    # Detect Hertha-style: mostly short indented lines with blanks between each
    # Check if >60% of non-blank lines are short (<50 chars) and there are many blank lines
    non_blank = [l for l in lines if l.strip()]
    blank_count = sum(1 for l in lines if not l.strip())
    short_count = sum(1 for l in non_blank if len(l.strip()) < 50)
    is_hertha_style = (len(non_blank) > 10 and
                       short_count / len(non_blank) > 0.6 and
                       blank_count > len(non_blank) * 0.5)

    if is_hertha_style:
        return _parse_hertha_style(lines)

    # Normal: stanzas separated by blank lines
    stanzas = []
    current = []
    for line in lines:
        if not line.strip():
            if current:
                stanzas.append({"stanza_num": "", "lines": current})
                current = []
        else:
            current.append(line.strip())
    if current:
        stanzas.append({"stanza_num": "", "lines": current})
    return stanzas


def _parse_hertha_style(lines: list[str]) -> list[dict]:
    """Parse Hertha/Garden of Proserpine style: short indented lines with
    blanks between each, 5-line stanzas (4 short + 1 long closing line).

    Strategy: collect all non-blank lines, then group into stanzas.
    Hertha stanzas are 5 lines each (4 short + 1 long).
    Garden stanzas are 8 lines each (6 short + 2 medium).
    We use blank-line gaps of 2+ to detect stanza boundaries.
    """
    # Collect all non-blank lines with their positions
    verse_lines = []
    gap_sizes = []  # gap before each line (number of consecutive blanks)
    consecutive_blanks = 0

    for line in lines:
        if not line.strip():
            consecutive_blanks += 1
        else:
            gap_sizes.append(consecutive_blanks)
            verse_lines.append(line.strip())
            consecutive_blanks = 0

    if not verse_lines:
        return []

    # Find the typical "big gap" size that separates stanzas
    # In Hertha: small gaps (1 blank) between lines within a stanza,
    # bigger gaps (2-3 blanks) between stanzas
    # Use gaps >= 2 as stanza breaks
    stanzas = []
    current = [verse_lines[0]]

    for i in range(1, len(verse_lines)):
        if gap_sizes[i] >= 2:
            if current:
                stanzas.append({"stanza_num": "", "lines": current})
                current = []
        current.append(verse_lines[i])

    if current:
        stanzas.append({"stanza_num": "", "lines": current})

    return stanzas


def _parse_roman_sections(lines: list[str]) -> list[dict]:
    """Parse Ave Atque Vale style with Roman numeral section markers.

    The file has blank lines between every verse line, so we can't use
    blank lines as stanza breaks. Instead: Roman numerals are section breaks,
    and each section is one stanza.
    """
    stanzas = []
    current = []
    current_num = ""

    roman_re = re.compile(r"^(I{1,3}|IV|V|VI{0,3}|IX|X{1,3}|XI{1,3}|XIV|XV|XVI{0,3}|XVIII?)$")

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue  # Skip ALL blank lines — they're formatting artifacts

        if roman_re.match(stripped):
            if current:
                stanzas.append({"stanza_num": current_num, "lines": current})
                current = []
            current_num = stripped
            continue

        current.append(stripped)

    if current:
        stanzas.append({"stanza_num": current_num, "lines": current})
    return stanzas


def parse():
    with open(RAW_PATH) as f:
        all_lines = f.readlines()

    poems = []
    total_lines = 0

    for idx, (start, title, date) in enumerate(POEMS):
        # End line is start of next poem (or end of file)
        if idx + 1 < len(POEMS):
            end = POEMS[idx + 1][0] - 1
        else:
            end = len(all_lines)

        block = all_lines[start - 1:end]
        # Skip the title line itself
        # Find the first non-blank line that matches the title
        skip = 0
        for i, line in enumerate(block):
            stripped = line.strip()
            if stripped and (stripped.upper() == title.upper() or
                           stripped == title or
                           stripped.replace("Trimph", "Triumph") == title):
                skip = i + 1
                break

        if skip:
            block = block[skip:]

        stanzas = _parse_poem_block(block, title)
        n_lines = sum(len(s["lines"]) for s in stanzas)
        total_lines += n_lines
        print(f"    {title:40s} {len(stanzas):4d} st  {n_lines:5d} ln")

        poems.append({
            "title": title,
            "stanzas": stanzas,
        })

    print(f"\n  Total: {len(poems)} poems, {total_lines} lines")

    output = {
        "tcp_id": "",
        "gutenberg_id": "",
        "author": "Algernon Charles Swinburne",
        "title": "Selected Poems (OTA #1243)",
        "date": "1866",
        "source": "Oxford Text Archive #1243",
        "poems": poems,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"  Written: {OUT_PATH}")


if __name__ == "__main__":
    parse()
