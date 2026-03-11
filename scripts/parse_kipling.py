#!/usr/bin/env python3
"""
parse_kipling.py — Parse Rudyard Kipling's poetry from four Project Gutenberg
plaintext files into a single intermediate JSON.

Input files (corpus/raw/gutenberg/):
  - kipling_ballads.txt        (#7846)  Departmental Ditties + Ballads & Barrack-Room Ballads
  - kipling_seven_seas.txt     (#27870) The Seven Seas
  - kipling_five_nations_1.txt (#60260) The Five Nations Vol I
  - kipling_five_nations_2.txt (#60261) The Five Nations Vol II

Output: corpus/intermediate/GUT_kipling-poems.json

Usage:
    python scripts/parse_kipling.py
"""

import json
import re
from pathlib import Path

RAW_DIR = Path("corpus/raw/gutenberg")
OUT_PATH = Path("corpus/intermediate/GUT_kipling-poems.json")

FILES = [
    ("7846", RAW_DIR / "kipling_ballads.txt"),
    ("27870", RAW_DIR / "kipling_seven_seas.txt"),
    ("60260", RAW_DIR / "kipling_five_nations_1.txt"),
    ("60261", RAW_DIR / "kipling_five_nations_2.txt"),
]

# Section headers that should NOT become poems (matched after stripping
# [Page N], trailing period, and leading/trailing whitespace)
SECTION_HEADERS = {
    "DEPARTMENTAL DITTIES",
    "DEPARTMENTAL DITTIES AND OTHER VERSES",
    "BALLADS AND BARRACK ROOM BALLADS",
    "BALLADS AND BARRACK-ROOM BALLADS",
    "BALLADS",
    "BARRACK-ROOM BALLADS",
    "CONTENTS",
    "THE SEVEN SEAS",
    "THE FIVE NATIONS",
    "SERVICE SONGS",
    "FOOTNOTES",
    "INDEX TO FIRST LINES",
    "INDEX OF FIRST LINES",
}

# Substrings that mark front-matter lines to skip
FRONT_MATTER_SUBS = [
    "VOLUME I", "VOLUME II", "VOL. I", "VOL. II",
    "IN TWO VOLUMES", "THE SERVICE EDITION", "THE WORKS OF",
    "BY RUDYARD KIPLING", "RUDYARD KIPLING", "Rudyard Kipling",
    "Author of ", "Barrack-Room Ballads,", "The Jungle Books,", "Etc.",
    "New York", "D. Appleton and Company", "Copyright,",
    "by Rudyard Kipling", "This book is also protected",
    "Britain, and the several", "severally copyrighted",
    "METHUEN AND CO", "36 ESSEX STREET",
    "_First Published_", "_Second Edition_", "_Third Edition_",
    "_Fourth Edition_", "_Fifth ", "_Sixth ", "_Seventh ", "_Eighth ",
    "_Ninth ", "_Tenth", "_Eleventh", "_Twelfth", "_Thirteenth",
    "_Fourteenth", "_Fifteenth",
    "Transcriber's Note", "Italic text is enclosed",
    "Bracketed page numbers", "Poem, were added",
    "Contents is in alphabetical",
    "Variant and dialect", "errors have been corrected",
    "amendments have been listed", "amended to",
    "Printed by T. and A. CONSTABLE", "Edinburgh University Press",
    "The text contains many", "to be intentional",
    "pp. iii", "START: FULL LICENSE", "THE FULL PROJECT GUTENBERG",
    "PLEASE READ THIS BEFORE",
    "Transcriber remedied",
    "Produced by Ted Garvin", "Produced by deaurider",
    "Produced by Stephen Hope",
    "E-text prepared by",
    "Project Gutenberg Online",
    "(http://www.pgdp",
    "Transcriber\u2019s Notes",
    "Transcriber's Notes",
    "Transcriber's Note:",
    "Transcriber\u2019s Note:",
    "to have been done deliberately",
]


# ---------------------------------------------------------------------------
# Title helpers
# ---------------------------------------------------------------------------

def _title_case(text: str) -> str:
    """Smart title-casing for ALL CAPS text."""
    small_words = {"a", "an", "the", "and", "but", "or", "nor", "for", "in",
                   "on", "at", "to", "of", "by", "o'"}
    words = text.split()
    result = []
    for i, w in enumerate(words):
        if w.startswith('"') or w.startswith("'"):
            prefix = w[0]
            rest = w[1:]
            result.append(prefix + _cap_word(rest))
            continue
        wl = w.lower().rstrip(".,;:!?-")
        if i > 0 and wl in small_words:
            result.append(w.lower())
        else:
            result.append(_cap_word(w))
    return " ".join(result)


def _cap_word(word: str) -> str:
    """Capitalize first alpha char, lowercase rest. Handles Mc/Mac prefixes."""
    if not word:
        return word
    result = []
    found_alpha = False
    for c in word:
        if c.isalpha() and not found_alpha:
            result.append(c.upper())
            found_alpha = True
        elif c.isalpha():
            result.append(c.lower())
        else:
            result.append(c)
    capped = "".join(result)
    # Handle Mc prefix: McAndrews, McAndrew
    if capped.startswith("Mc") and len(capped) > 2 and capped[2].islower():
        capped = "Mc" + capped[2].upper() + capped[3:]
    return capped


def _clean_raw_title(raw: str) -> str:
    """Remove [Page N], trailing period, surrounding quotes."""
    raw = re.sub(r"\s*\[Page [ivxlcdm\d]+\]", "", raw).strip()
    raw = raw.rstrip(".")
    if len(raw) > 2 and raw[0] == '"' and raw[-1] == '"':
        raw = raw[1:-1].strip()
    elif len(raw) > 2 and raw[0] == "'" and raw[-1] == "'":
        raw = raw[1:-1].strip()
    return raw


def _normalize_title(raw: str) -> str:
    """Clean and title-case a poem title."""
    raw = _clean_raw_title(raw)
    alpha = [c for c in raw if c.isalpha()]
    if alpha and sum(1 for c in alpha if c.isupper()) / len(alpha) > 0.7:
        raw = _title_case(raw)
    return raw.strip()


# ---------------------------------------------------------------------------
# Line classifiers
# ---------------------------------------------------------------------------

def _is_mostly_upper(text: str, threshold: float = 0.7) -> bool:
    alpha = [c for c in text if c.isalpha()]
    if len(alpha) < 2:
        return False
    return sum(1 for c in alpha if c.isupper()) / len(alpha) > threshold


def _count_preceding_blanks(lines: list[str], idx: int) -> int:
    """Count blank lines immediately before lines[idx]."""
    count = 0
    j = idx - 1
    while j >= 0 and not lines[j].strip():
        count += 1
        j -= 1
    return count


def _is_allcaps_title(lines: list[str], idx: int) -> bool:
    """Check if lines[idx] is an ALL CAPS poem/section title.

    Key heuristic: real titles are preceded by 3+ blank lines (sometimes 2
    for FOOTNOTES). In-verse ALL CAPS text has at most 1 blank line before it.
    """
    line = lines[idx]
    stripped = line.strip()
    if not stripped:
        return False

    # Remove [Page N] for checking
    clean = re.sub(r"\s*\[Page [ivxlcdm\d]+\]", "", stripped).strip()
    if not _is_mostly_upper(clean):
        return False

    # Exclude bare Roman numerals (I, II, III, IV, etc.)
    if _is_roman_numeral_line(clean):
        return False

    # Exclude "PAGE" (index header)
    if clean.rstrip(".") == "PAGE":
        return False

    # Must be preceded by at least 2 blank lines (3+ for poems, 2 for FOOTNOTES)
    blanks_before = _count_preceding_blanks(lines, idx)
    return blanks_before >= 2


def _is_mixed_case_title(lines: list[str], idx: int) -> bool:
    """Check if lines[idx] is a mixed-case sub-poem title.

    These appear in Seven Seas within compound poems like 'A Song of the English'.
    E.g. 'The Coastwise Lights.', 'The Song of the Dead.'
    Does NOT match italic city names (e.g. '_Bombay._') — those stay as stanza markers.
    Does NOT match bare Roman numerals.
    """
    line = lines[idx]
    stripped = line.strip()
    if not stripped or len(stripped) > 60:
        return False
    # Must start at column 0
    if line[0] in (" ", "\t"):
        return False
    # Exclude bare Roman numerals
    if _is_roman_numeral_line(stripped):
        return False
    # Must start with a capital letter (not underscore — city names stay in parent poem)
    if not stripped[0].isupper():
        return False
    # Must NOT be mostly uppercase (those are allcaps titles)
    if _is_mostly_upper(stripped):
        return False
    # Short, title-like
    if len(stripped.rstrip(".").split()) > 8:
        return False
    # Must be preceded by 2+ blank lines
    blanks_before = _count_preceding_blanks(lines, idx)
    if blanks_before < 2:
        return False
    # Exclude certain false positives
    clean_for_check = stripped.rstrip(".").rstrip(":")
    if clean_for_check in ("Page", "Amen"):
        return False
    if "Transcriber" in clean_for_check:
        return False
    return True


def _is_section_header(stripped: str) -> bool:
    clean = re.sub(r"\s*\[Page [ivxlcdm\d]+\]", "", stripped).strip().rstrip(".")
    return clean in SECTION_HEADERS


def _is_front_matter(stripped: str) -> bool:
    for pat in FRONT_MATTER_SUBS:
        if pat in stripped:
            return True
    if stripped in ("[Illustration]", "Amen.", "OF", "of"):
        return True
    return False


def _is_separator(stripped: str) -> bool:
    return bool(re.match(r"^[\s*]+$", stripped) and "*" in stripped)


def _is_page_marker(stripped: str) -> bool:
    return bool(re.match(r"^\[Page [ivxlcdm\d]+\]\s*$", stripped))


def _is_roman_numeral_line(stripped: str) -> bool:
    """Match Roman numeral markers: 'I.', 'II.', 'IV', 'III' (with or without period)."""
    return bool(re.match(r"^[IVXLC]+\.?\s*$", stripped))


def _is_parenthetical_subtitle(stripped: str) -> bool:
    return bool(re.match(r"^\(.*\)$", stripped))


def _is_date_line(stripped: str) -> bool:
    if re.match(r"^\(\d{4}\)$", stripped):
        return True
    if re.match(r"^[A-Z]+ \d+,? \d{4}$", stripped):
        return True
    return False


def _is_footnote_ref(stripped: str) -> bool:
    """Inline footnote like '[1] Head-groom.' or '[2] Slang.'."""
    return bool(re.match(r"^\[\d+\]\s+", stripped))


# ---------------------------------------------------------------------------
# TOC / front-matter skipping
# ---------------------------------------------------------------------------

def _find_content_start(lines: list[str]) -> int:
    """Find line index where actual poem content begins (after all TOC/index)."""
    last_skip = 0
    in_toc = False
    in_index = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        if stripped in ("CONTENTS", "CONTENTS."):
            in_toc = True
            last_skip = i
            continue

        if "INDEX TO FIRST LINES" in stripped or "INDEX OF FIRST LINES" in stripped:
            in_index = True
            last_skip = i
            continue

        if in_toc or in_index:
            if not stripped:
                continue
            # Lines with trailing page numbers or roman numerals
            if re.search(r"[\divxlc]+\s*$", stripped):
                last_skip = i
                continue
            # Sub-headers within TOC
            clean = re.sub(r"\s*\[Page [ivxlcdm\d]+\]", "", stripped).strip().rstrip(".")
            if clean in SECTION_HEADERS:
                last_skip = i
                continue
            in_toc = False
            in_index = False

    return last_skip + 1 if last_skip > 0 else 0


# ---------------------------------------------------------------------------
# Subtitle consumption
# ---------------------------------------------------------------------------

def _consume_post_title(lines: list[str], i: int) -> tuple[str, int]:
    """After an ALL CAPS title, consume blank lines, subtitles, epigraphs,
    and dates. Returns (subtitle_text_to_append, next_index)."""
    subtitle_parts = []

    while i < len(lines):
        stripped = lines[i].strip()

        # Skip blank lines
        if not stripped:
            i += 1
            continue

        # Date lines — skip
        if _is_date_line(stripped):
            i += 1
            continue

        # Parenthetical subtitle — incorporate (but only if at left margin,
        # not indented verse like "(May the Lord amend her!)")
        leading = len(lines[i]) - len(lines[i].lstrip())
        if _is_parenthetical_subtitle(stripped) and leading < 3:
            sub = stripped[1:-1].strip()
            if _is_mostly_upper(sub):
                sub = _title_case(sub)
            subtitle_parts.append(f"({sub})")
            i += 1
            continue

        # Short ALL CAPS subtitle (e.g. "C. J. RHODES, buried in the Matoppos,")
        # Only if at column 0 (not indented — indented lines are verse)
        leading = len(lines[i]) - len(lines[i].lstrip())
        if leading == 0 and len(stripped) < 60:
            # Prose metadata line ending with comma at left margin
            if stripped.endswith(",") and not stripped.startswith("_"):
                i += 1
                continue

            # Mixed-case subtitle like "To The City Of Bombay." or
            # "The American Spirit speaks:" — skip
            if not _is_mostly_upper(stripped) and len(stripped.split()) <= 6:
                i += 1
                continue

        # Short ALL CAPS subtitle line that's not another poem title
        # (not preceded by 3+ blank lines)
        if _is_mostly_upper(stripped) and len(stripped) < 50 and leading < 3:
            blanks = _count_preceding_blanks(lines, i)
            if blanks < 2:
                # e.g. "ENGLISH IRREGULAR: '99-02" after CHANT-PAGAN
                i += 1
                continue

        # Multi-line italic epigraph note
        if stripped.startswith("_Being ") or stripped.startswith("_'"):
            while i < len(lines) and lines[i].strip():
                if lines[i].strip().endswith("_"):
                    i += 1
                    break
                i += 1
            continue

        # Short quoted epigraph (single line in double quotes)
        if stripped.startswith('"') and stripped.endswith('"') and len(stripped) < 80:
            i += 1
            continue

        # Single-line prose epigraph in single quotes
        if (stripped.startswith("'") and stripped.endswith("'")
                and len(stripped) < 80 and not _is_mostly_upper(stripped)):
            i += 1
            continue

        # District Orders type notes (italicized multi-line)
        if stripped.startswith("_District Orders") or stripped.startswith("_'and will"):
            while i < len(lines) and lines[i].strip():
                i += 1
            continue

        # Otherwise this is the start of verse
        break

    return " ".join(subtitle_parts), i


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_file(filepath: Path, gutenberg_id: str) -> list[dict]:
    """Parse a single Kipling Gutenberg file into a list of poems."""
    print(f"\n--- Parsing {filepath.name} (#{gutenberg_id}) ---")

    with open(filepath, encoding="utf-8") as f:
        raw = f.read()

    # Strip Gutenberg boilerplate
    m = re.search(r"\*\*\* START OF.*?\*\*\*", raw)
    if m:
        raw = raw[m.end():]
    m = re.search(r"\*\*\* END OF", raw)
    if m:
        raw = raw[:m.start()]

    lines = raw.split("\n")
    print(f"  {len(lines)} lines after stripping boilerplate")

    start = _find_content_start(lines)
    print(f"  Content begins at line {start}")

    poems = []
    current_title = None
    current_stanzas = []
    current_stanza_lines = []
    in_footnotes = False

    def flush_stanza():
        nonlocal current_stanza_lines
        if current_stanza_lines:
            current_stanzas.append({
                "stanza_num": "",
                "lines": current_stanza_lines,
            })
            current_stanza_lines = []

    def flush_poem():
        nonlocal current_title, current_stanzas, current_stanza_lines
        flush_stanza()
        if current_title and current_stanzas:
            # Strip trailing footnote stanzas (all lines start with digit + space)
            while current_stanzas:
                last = current_stanzas[-1]
                if all(re.match(r"^\d+ ", ln) for ln in last["lines"]):
                    current_stanzas.pop()
                else:
                    break
            if current_stanzas:
                poems.append({
                    "title": current_title,
                    "stanzas": current_stanzas,
                })
        current_title = None
        current_stanzas = []
        current_stanza_lines = []

    def start_poem(title: str):
        nonlocal current_title
        flush_poem()
        current_title = title

    i = start
    while i < len(lines):
        stripped = lines[i].strip()

        # Skip blanks (stanza break)
        if not stripped:
            if current_stanza_lines:
                flush_stanza()
            i += 1
            continue

        # Skip separators, page markers, illustrations, front-matter
        if _is_separator(stripped) or _is_page_marker(stripped):
            i += 1
            continue
        if stripped.startswith("[Illustration"):
            i += 1
            continue
        if _is_front_matter(stripped):
            i += 1
            continue

        # FOOTNOTES section
        if stripped in ("FOOTNOTES:", "FOOTNOTES") and _count_preceding_blanks(lines, i) >= 2:
            in_footnotes = True
            i += 1
            continue

        if in_footnotes:
            if _is_allcaps_title(lines, i) and not _is_section_header(stripped):
                in_footnotes = False
                # fall through
            else:
                i += 1
                continue

        # Inline footnotes
        if _is_footnote_ref(stripped):
            i += 1
            continue

        # Section headers
        if _is_allcaps_title(lines, i) and _is_section_header(stripped):
            clean = re.sub(r"\s*\[Page [ivxlcdm\d]+\]", "", stripped).strip().rstrip(".")
            flush_poem()

            # Special: "DEPARTMENTAL DITTIES" — the Prelude poem follows
            if clean == "DEPARTMENTAL DITTIES":
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and not _is_allcaps_title(lines, j):
                    current_title = "Prelude"
                    i = j
                    continue

            # Special: "SERVICE SONGS" — prelude follows
            if clean == "SERVICE SONGS":
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and not _is_allcaps_title(lines, j):
                    current_title = "Service Songs (Prelude)"
                    i = j
                    continue

            # Special: "BARRACK-ROOM BALLADS" — dedication/epigraph follows
            if clean == "BARRACK-ROOM BALLADS":
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and not _is_allcaps_title(lines, j):
                    next_s = lines[j].strip()
                    if next_s.lower().startswith("dedication"):
                        current_title = "Barrack-Room Ballads: Dedication"
                    else:
                        current_title = "Barrack-Room Ballads (Epigraph)"
                    i = j
                    continue

            i += 1
            continue

        # ALL CAPS poem title
        if _is_allcaps_title(lines, i):
            title = _normalize_title(stripped)
            start_poem(title)

            # Consume subtitles, epigraphs, dates
            subtitle, i = _consume_post_title(lines, i + 1)
            if subtitle:
                current_title = f"{current_title} {subtitle}"
            continue

        # Mixed-case sub-poem title (Seven Seas compound poems)
        if _is_mixed_case_title(lines, i):
            clean = _clean_raw_title(stripped)
            # Remove italic markers for city names like "_Bombay._"
            if clean.startswith("_") and clean.endswith("_"):
                clean = clean[1:-1].strip().rstrip(".")
            start_poem(clean)
            i += 1
            continue

        # Roman numeral stanza markers
        if _is_roman_numeral_line(stripped):
            flush_stanza()
            i += 1
            continue

        # Verse line
        if current_title is not None:
            current_stanza_lines.append(stripped)

        i += 1

    flush_poem()
    print(f"  Extracted {len(poems)} poems")
    return poems


# ---------------------------------------------------------------------------
# Dedup and output
# ---------------------------------------------------------------------------

def parse():
    all_poems = []
    seen_titles = set()

    for gut_id, filepath in FILES:
        if not filepath.exists():
            print(f"  WARNING: {filepath} not found, skipping")
            continue
        poems = parse_file(filepath, gut_id)

        for poem in poems:
            title_key = poem["title"].lower().strip()
            if title_key in seen_titles:
                print(f"  DEDUP: Skipping '{poem['title']}' (already seen)")
                continue
            seen_titles.add(title_key)
            all_poems.append(poem)

    # Summary
    total_stanzas = sum(len(p["stanzas"]) for p in all_poems)
    total_lines = sum(
        sum(len(s["lines"]) for s in p["stanzas"])
        for p in all_poems
    )
    print(f"\n=== Summary ===")
    print(f"  {len(all_poems)} poems, {total_stanzas} stanzas, {total_lines} lines")

    for p in all_poems:
        n_st = len(p["stanzas"])
        n_ln = sum(len(s["lines"]) for s in p["stanzas"])
        print(f"    {p['title']:65s} {n_st:3d} st  {n_ln:4d} ln")

    output = {
        "tcp_id": "",
        "gutenberg_id": "7846+27870+60260+60261",
        "author": "Rudyard Kipling",
        "title": "Collected Verse (Departmental Ditties, Barrack-Room Ballads, Seven Seas, Five Nations)",
        "date": "1903",
        "source": "Gutenberg",
        "poems": all_poems,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n  Written: {OUT_PATH}")


if __name__ == "__main__":
    parse()
