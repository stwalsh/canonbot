#!/usr/bin/env python3
"""
parse_dickinson.py — Parse Gutenberg #12242 Dickinson complete poems into intermediate JSON.

Plaintext format:
  - Three series, each with sections (I. LIFE., II. LOVE., III. NATURE., IV. TIME AND ETERNITY.)
  - Each poem numbered with Roman numerals on their own line (I., II., XLII., etc.)
  - Most poems have an ALL CAPS title on the next line(s)
  - Some poems have no title (verse starts directly after numeral)
  - Bracketed notes like [Published in "A Masque of Poets"...] should be skipped
  - Stanzas separated by blank lines
  - Some words in _italic_ markers
  - Epigraph poems (unnumbered, between series title pages and section headers) are included
  - Prefaces, transcriber's notes, and series title pages are skipped

Usage:
    python scripts/parse_dickinson.py
"""

import json
import re
from pathlib import Path

RAW_PATH = Path("corpus/raw/gutenberg/dickinson_complete.txt")
OUT_PATH = Path("corpus/intermediate/GUT_dickinson-complete.json")

# Section headers to skip (these are NOT poem titles)
SECTION_HEADER_RE = re.compile(
    r"^(I|II|III|IV|V)\.\s+(LIFE|LOVE|NATURE|TIME AND ETERNITY)\.\s*$"
)

# Roman numeral poem number on its own line
POEM_NUM_RE = re.compile(r"^([IVXLC]+)\.\s*$")

# Bracketed editorial notes
BRACKET_NOTE_RE = re.compile(r"^\[.*\]\s*$")
BRACKET_START_RE = re.compile(r"^\[")
BRACKET_END_RE = re.compile(r"\]\s*$")

# Series title pages and preface markers
SERIES_PAGE_RE = re.compile(r"^(POEMS|by EMILY DICKINSON|Second Series|Third Series|"
                            r"Edited by.*|MABEL LOOMIS TODD.*|T\.W\. HIGGINSON.*|"
                            r"PREFACE\.|TRANSCRIBER'S NOTE)\s*$", re.IGNORECASE)

# ALL CAPS title: at least 2 uppercase letters, possibly with punctuation/accents/quotes
ALLCAPS_TITLE_RE = re.compile(r'^[""]?[A-ZÀ-Ý][A-ZÀ-Ý\s,\'\'\-\.!\?;:\'""\u2018\u2019]+$')


def roman_to_int(s: str) -> int:
    """Convert a Roman numeral string to integer."""
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    result = 0
    for i, ch in enumerate(s):
        if i + 1 < len(s) and values.get(ch, 0) < values.get(s[i + 1], 0):
            result -= values.get(ch, 0)
        else:
            result += values.get(ch, 0)
    return result


def clean_title(raw: str) -> str:
    """Title-case an ALL CAPS title, strip trailing period, clean up."""
    # Strip surrounding quotes (curly or straight)
    raw = raw.strip().strip('"""\u201c\u201d')
    # Strip trailing period
    raw = raw.strip().rstrip(".")
    # Title case
    raw = raw.title()
    # Fix common title-case issues: 'S → 's, 'T → 't
    raw = re.sub(r"'S\b", "'s", raw)
    raw = re.sub(r"'T\b", "'t", raw)
    raw = re.sub(r"\bTo\b", "to", raw)
    raw = re.sub(r"\bOf\b", "of", raw)
    raw = re.sub(r"\bThe\b", "the", raw)
    raw = re.sub(r"\bIn\b", "in", raw)
    raw = re.sub(r"\bA\b", "a", raw)
    raw = re.sub(r"\bAn\b", "an", raw)
    raw = re.sub(r"\bAnd\b", "and", raw)
    raw = re.sub(r"\bAt\b", "at", raw)
    raw = re.sub(r"\bFor\b", "for", raw)
    raw = re.sub(r"\bOn\b", "on", raw)
    raw = re.sub(r"\bWith\b", "with", raw)
    raw = re.sub(r"\bFrom\b", "from", raw)
    raw = re.sub(r"\bBut\b", "but", raw)
    raw = re.sub(r"\bBy\b", "by", raw)
    raw = re.sub(r"\bOr\b", "or", raw)
    raw = re.sub(r"\bNor\b", "nor", raw)
    raw = re.sub(r"\bNot\b", "not", raw)
    raw = re.sub(r"\bNo\b", "no", raw)
    raw = re.sub(r"\bIs\b", "is", raw)
    # First word should always be capitalized
    if raw:
        raw = raw[0].upper() + raw[1:]
    return raw


def strip_italics(text: str) -> str:
    """Remove _italic_ markers from text."""
    return re.sub(r"_([^_]+)_", r"\1", text)


def is_preface_or_boilerplate(line: str) -> bool:
    """Check if a line belongs to preface/boilerplate material."""
    stripped = line.strip()
    if SERIES_PAGE_RE.match(stripped):
        return True
    return False


def lines_to_stanzas(lines: list[str]) -> list[dict]:
    """Split a list of verse lines into stanzas (separated by blank lines)."""
    stanzas = []
    current = []
    for line in lines:
        if not line.strip():
            if current:
                stanzas.append({"stanza_num": "", "lines": current})
                current = []
        else:
            cleaned = strip_italics(line.rstrip())
            if cleaned.strip():
                current.append(cleaned.strip())
    if current:
        stanzas.append({"stanza_num": "", "lines": current})
    return stanzas


def parse():
    print("Parsing Dickinson complete poems (Gutenberg #12242)")

    with open(RAW_PATH, encoding="utf-8") as f:
        all_lines = f.readlines()

    print(f"  Total lines in file: {len(all_lines)}")

    # Find content boundaries
    start_idx = None
    end_idx = len(all_lines)

    for i, line in enumerate(all_lines):
        if "*** START OF" in line:
            start_idx = i + 1
        if "*** END OF" in line:
            end_idx = i
            break

    if start_idx is None:
        print("ERROR: Could not find *** START OF marker")
        return

    print(f"  Content region: lines {start_idx + 1}–{end_idx}")

    # Find where actual poems begin (after preface + transcriber's note + epigraph)
    # The first section header "I. LIFE." marks the start of poems
    first_section = None
    for i in range(start_idx, end_idx):
        stripped = all_lines[i].strip()
        if SECTION_HEADER_RE.match(stripped):
            first_section = i
            break

    if first_section is None:
        print("ERROR: Could not find first section header (I. LIFE.)")
        return

    print(f"  First section header at line {first_section + 1}: {all_lines[first_section].strip()}")

    # Now parse poems from first_section onward
    # Strategy: scan line by line, detect poem starts by Roman numeral pattern,
    # skip section headers, skip preface blocks between series
    poems = []
    current_section = ""
    current_series = "First Series"
    global_poem_num = 0

    i = first_section
    while i < end_idx:
        line = all_lines[i].rstrip("\n")
        stripped = line.strip()

        # Skip blank lines
        if not stripped:
            i += 1
            continue

        # Detect section headers
        if SECTION_HEADER_RE.match(stripped):
            current_section = stripped
            print(f"  [{current_series}] Section: {current_section}")
            i += 1
            continue

        # Detect series transitions (POEMS / by EMILY DICKINSON / Second Series etc.)
        if stripped == "POEMS" and i + 2 < end_idx:
            next_stripped = all_lines[i + 1].strip()
            if next_stripped == "" or "EMILY DICKINSON" in all_lines[i + 2].strip().upper():
                # This is a series title page — skip until next section header
                if "Second" in " ".join(
                    all_lines[j].strip() for j in range(i, min(i + 10, end_idx))
                ):
                    current_series = "Second Series"
                elif "Third" in " ".join(
                    all_lines[j].strip() for j in range(i, min(i + 10, end_idx))
                ):
                    current_series = "Third Series"
                # Skip until we hit a section header or a poem numeral
                i += 1
                while i < end_idx:
                    s = all_lines[i].strip()
                    if SECTION_HEADER_RE.match(s) or POEM_NUM_RE.match(s):
                        break
                    i += 1
                continue

        # Detect PREFACE. blocks — skip until next section header
        if stripped == "PREFACE.":
            i += 1
            while i < end_idx:
                s = all_lines[i].strip()
                if SECTION_HEADER_RE.match(s) or POEM_NUM_RE.match(s):
                    break
                i += 1
            continue

        # Detect epigraph poems (indented verse without a Roman numeral, between
        # series header and first section header)
        # These appear as indented lines before the first section in each series.
        # We'll capture them as unnumbered poems.
        # Actually, check: is this an indented line that looks like verse?
        if line.startswith("    ") and not POEM_NUM_RE.match(stripped):
            # Could be an epigraph poem — gather lines until we hit a section header
            # or series page material
            epigraph_lines = []
            while i < end_idx:
                ln = all_lines[i].rstrip("\n")
                s = ln.strip()
                if SECTION_HEADER_RE.match(s) or POEM_NUM_RE.match(s):
                    break
                if s == "PREFACE." or SERIES_PAGE_RE.match(s):
                    break
                epigraph_lines.append(ln)
                i += 1

            stanzas = lines_to_stanzas(epigraph_lines)
            if stanzas:
                # Use first line as title
                first_line = stanzas[0]["lines"][0] if stanzas[0]["lines"] else "Untitled"
                # Trim to a reasonable title length
                title = first_line.split(",")[0].split("--")[0].strip().rstrip(".")
                if len(title) > 60:
                    title = title[:57] + "..."
                global_poem_num += 1
                n_lines = sum(len(s["lines"]) for s in stanzas)
                poems.append({"title": title, "stanzas": stanzas})
                print(f"    [epigraph] {title:50s} {len(stanzas):3d} st  {n_lines:4d} ln")
            continue

        # Detect poem start: Roman numeral on its own line
        m = POEM_NUM_RE.match(stripped)
        if m:
            roman = m.group(1)
            local_num = roman_to_int(roman)
            global_poem_num += 1

            i += 1  # Move past numeral line

            # Skip blank lines after numeral
            while i < end_idx and not all_lines[i].strip():
                i += 1

            if i >= end_idx:
                break

            # Check for ALL CAPS title
            title = None
            title_lines_raw = []
            while i < end_idx:
                s = all_lines[i].strip()
                if not s:
                    break
                if ALLCAPS_TITLE_RE.match(s):
                    title_lines_raw.append(s)
                    i += 1
                else:
                    break

            if title_lines_raw:
                raw_title = " ".join(title_lines_raw)
                title = clean_title(raw_title)
                # Skip blank lines after title
                while i < end_idx and not all_lines[i].strip():
                    i += 1

            # Skip bracketed notes
            if i < end_idx and BRACKET_START_RE.match(all_lines[i].strip()):
                while i < end_idx:
                    s = all_lines[i].strip()
                    i += 1
                    if BRACKET_END_RE.search(s):
                        break
                # Skip blank lines after note
                while i < end_idx and not all_lines[i].strip():
                    i += 1

            # Now gather verse lines until next poem number, section header, or series page
            verse_lines = []
            while i < end_idx:
                ln = all_lines[i].rstrip("\n")
                s = ln.strip()

                # Stop conditions
                if POEM_NUM_RE.match(s):
                    break
                if SECTION_HEADER_RE.match(s):
                    break
                if s == "POEMS":
                    break
                if s == "PREFACE.":
                    break
                # "End of Project Gutenberg's..." line
                if s.startswith("End of Project Gutenberg"):
                    break

                verse_lines.append(ln)
                i += 1

            stanzas = lines_to_stanzas(verse_lines)

            if stanzas:
                if title is None:
                    # Use first line as title
                    first_line = stanzas[0]["lines"][0] if stanzas[0]["lines"] else f"Poem {roman}"
                    # Use first line up to first dash pair or end
                    title = re.split(r"\s--\s", first_line)[0].strip().rstrip(",;.")
                    if len(title) > 60:
                        title = title[:57] + "..."

                n_lines = sum(len(s["lines"]) for s in stanzas)
                poems.append({"title": title, "stanzas": stanzas})
                print(f"    {roman:>6s}. {title:50s} {len(stanzas):3d} st  {n_lines:4d} ln")
            continue

        # Skip any other unrecognized lines (e.g. stray boilerplate)
        i += 1

    total_lines = sum(sum(len(s["lines"]) for s in p["stanzas"]) for p in poems)
    print(f"\n  Total: {len(poems)} poems, {total_lines} lines")

    output = {
        "tcp_id": "",
        "gutenberg_id": "12242",
        "author": "Emily Dickinson",
        "title": "Poems by Emily Dickinson, Three Series, Complete",
        "date": "1886",
        "source": "Gutenberg",
        "poems": poems,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"  Written: {OUT_PATH}")


if __name__ == "__main__":
    parse()
