#!/usr/bin/env python3
"""
parse_hopkins.py — Parse Gutenberg #22403 Hopkins poems into intermediate JSON.

Plaintext format:
  - Poems numbered with _N or _N_ prefix on its own line
  - Title follows (sometimes multi-line, in _italic_ markers)
  - Stanzas separated by blank lines
  - Editor's notes after poem 72 (skip)
  - Author's preface before poem 1 (skip)

Usage:
    python scripts/parse_hopkins.py
"""

import json
import re
from pathlib import Path

RAW_PATH = Path("corpus/raw/gutenberg/hopkins_poems.txt")
OUT_PATH = Path("corpus/intermediate/GUT_hopkins-poems.json")

# Lines to skip: Gutenberg boilerplate + preface ends, notes begin
POEMS_START_MARKER = "_1"
NOTES_MARKER = "EDITOR'S NOTES"

# Titles from editor's notes for poems that lack inline titles
TITLE_OVERRIDES = {
    6: "The Silver Jubilee",
    12: "The Windhover",
    27: "The Handsome Heart",
    31: "Spring and Fall",
    34: "As kingfishers catch fire",
    41: "No worst, there is none",
    42: "To seem the stranger",
    44: "I wake and feel the fell of dark",
    45: "Patience, hard thing!",
    46: "My own heart let me more have pity on",
    47: "Tom's Garland",
    53: "St. Winefred's Well",
    55: "What shall I do for the land that bred me",
    59: "The times are nightfall",
    60: "Hope holds to Christ",
    62: "Repeat that, repeat",
    66: "The shepherd's brow",
    69: "Thee, God, I come from",
    71: "Strike, churl",
}


def _clean_title(lines: list[str]) -> str:
    """Clean a title assembled from one or more lines."""
    text = " ".join(lines)
    # Strip italic markers
    text = text.replace("_", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse():
    with open(RAW_PATH, encoding="iso-8859-1") as f:
        all_lines = f.readlines()

    # Find where poems start and notes begin
    poems_start = None
    notes_start = len(all_lines)

    for i, line in enumerate(all_lines):
        stripped = line.rstrip("\n")
        if poems_start is None and stripped == POEMS_START_MARKER:
            poems_start = i
        if NOTES_MARKER in stripped:
            notes_start = i
            break

    if poems_start is None:
        print("ERROR: Could not find start of poems")
        return

    print(f"  Poems region: lines {poems_start+1}–{notes_start}")

    # Split into poem blocks by the _N or _N_ pattern
    poem_num_re = re.compile(r"^_(\d+)_?\s*$")

    poems = []
    current_num = None
    current_lines = []

    for i in range(poems_start, notes_start):
        line = all_lines[i].rstrip("\n")
        m = poem_num_re.match(line)
        if m:
            # Save previous poem
            if current_num is not None:
                poems.append((current_num, current_lines))
            current_num = int(m.group(1))
            current_lines = []
        else:
            current_lines.append(line)

    # Save last poem
    if current_num is not None:
        poems.append((current_num, current_lines))

    print(f"  Found {len(poems)} poem blocks")

    # Parse each poem block into title + stanzas
    result_poems = []
    total_lines = 0

    for num, lines in poems:
        # Strip leading/trailing blank lines
        while lines and not lines[0].strip():
            lines = lines[1:]
        while lines and not lines[-1].strip():
            lines = lines[:-1]

        if not lines:
            continue

        # Extract title: lines before the first verse line
        # Title lines are in _italic_ or are short non-verse text
        # Verse lines typically start with a capital letter or whitespace + capital
        title_lines = []
        verse_start = 0

        for j, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                # Blank line after title = verse starts after
                verse_start = j + 1
                break
            # Title lines contain italic markers or are very short
            if "_" in line or (len(stripped) < 60 and not stripped[0].isupper()):
                title_lines.append(stripped)
                verse_start = j + 1
            else:
                # Check if this looks like a title continuation
                # (subtitle like "A nun takes the veil")
                if j < 3 and len(stripped) < 50 and not any(
                    c in stripped for c in ".,;:!?"
                ):
                    title_lines.append(stripped)
                    verse_start = j + 1
                else:
                    break

        title = _clean_title(title_lines) if title_lines else f"Poem {num}"
        # Apply overrides for poems with titles only in editor's notes
        if num in TITLE_OVERRIDES and (title == f"Poem {num}" or len(title) > 80):
            title = TITLE_OVERRIDES[num]

        # Parse stanzas from remaining lines
        verse_lines = lines[verse_start:]
        stanzas = []
        current_stanza = []

        for line in verse_lines:
            if not line.strip():
                if current_stanza:
                    stanzas.append({"stanza_num": "", "lines": current_stanza})
                    current_stanza = []
            else:
                # Clean the line: strip leading/trailing whitespace but preserve
                # internal indentation patterns by keeping relative indent
                cleaned = line.rstrip()
                # Remove page number markers like (7) at start of line
                cleaned = re.sub(r"^\(\d+\)\s*", "", cleaned)
                if cleaned.strip():
                    current_stanza.append(cleaned.strip())

        if current_stanza:
            stanzas.append({"stanza_num": "", "lines": current_stanza})

        if stanzas:
            n_lines = sum(len(s["lines"]) for s in stanzas)
            total_lines += n_lines
            result_poems.append({
                "title": title,
                "stanzas": stanzas,
            })
            print(f"    {num:3d}. {title:50s} {len(stanzas):3d} st  {n_lines:4d} ln")

    print(f"\n  Total: {len(result_poems)} poems, {total_lines} lines")

    output = {
        "tcp_id": "",
        "gutenberg_id": "22403",
        "author": "Gerard Manley Hopkins",
        "title": "Poems of Gerard Manley Hopkins (1918)",
        "date": "1889",
        "source": "Gutenberg",
        "poems": result_poems,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"  Written: {OUT_PATH}")


if __name__ == "__main__":
    parse()
