#!/usr/bin/env python3
"""
parse_hardy.py — Parse Thomas Hardy's poetry from four Gutenberg plaintext files.

Input files (corpus/raw/gutenberg/):
  - hardy_wessex.txt        (#3167) — Wessex Poems
  - hardy_past_present.txt  (#3168) — Poems of the Past and the Present
  - hardy_laughingstocks.txt (#2997) — Time's Laughingstocks
  - hardy_late_lyrics.txt   (#4758) — Late Lyrics and Earlier

Output: corpus/intermediate/GUT_hardy-poems.json

The wessex.txt (#3167) is from a combined edition but only contains Wessex Poems.
The past_present.txt (#3168) is the standalone Poems of the Past and the Present.
Both are parsed; titles are deduplicated across all four files.

Usage:
    python scripts/parse_hardy.py
"""

import json
import re
from pathlib import Path

RAW_DIR = Path("corpus/raw/gutenberg")
OUT_PATH = Path("corpus/intermediate/GUT_hardy-poems.json")

FILES = [
    ("hardy_wessex.txt", "Wessex Poems"),
    ("hardy_past_present.txt", "Poems of the Past and the Present"),
    ("hardy_laughingstocks.txt", "Time's Laughingstocks"),
    ("hardy_late_lyrics.txt", "Late Lyrics and Earlier"),
]

# Section headers that divide groups of poems but are not poems themselves.
SECTION_HEADERS = {
    "WESSEX POEMS AND",
    "OTHER VERSES",
    "TIME'S",
    "LAUGHINGSTOCKS",
    "AND OTHER VERSES",
    "TIME'S LAUGHINGSTOCKS",
    "MORE LOVE LYRICS",
    "A SET OF COUNTRY SONGS",
    "PIECES OCCASIONAL AND VARIOUS",
    "WAR POEMS",
    "POEMS OF PILGRIMAGE",
    "MISCELLANEOUS POEMS",
    "IMITATIONS, ETC.",
    "RETROSPECT",
    "LATE LYRICS",
    "AND EARLIER",
    "WITH MANY OTHER VERSES",
    "ADDITIONS",
}

# Front-matter / boilerplate ALL CAPS lines to skip
SKIP_TITLES = {
    "BY THOMAS HARDY",
    "THOMAS HARDY",
    "MACMILLAN AND CO., LIMITED",
    "ST. MARTIN'S STREET, LONDON",
    "PRINTED IN GREAT BRITAIN",
    "BY R. & R. CLARK, LIMITED, EDINBURGH",
    "COPYRIGHT",
    "CONTENTS",
    "FOOTNOTES",
    "PREFACE",
    "APOLOGY",
}

# Regex patterns
DATE_LINE_RE = re.compile(r"^\d{3}[\d–-]\.$")             # "1866." or "187–."
DATE_RANGE_RE = re.compile(r"^\d{4}[-–]\d{2,4}\.$")     # "1866-67."
STAR_LINE_RE = re.compile(r"^\*\s*\*\s*\*\s*\*\s*\*$")  # "* * * * *"
PICTURE_RE = re.compile(r"^\s*\[Picture:.*\]$", re.IGNORECASE)
FOOTNOTE_MARKER_RE = re.compile(r"\{\d+\}")              # "{253}"
ROMAN_NUMERAL_RE = re.compile(r"^[IVXLC]+$")             # "I", "II", etc.
CONTENTS_PAGE_RE = re.compile(r"\s{4,}\d+\s*$")          # trailing page number
YEAR_LINE_RE = re.compile(r"^\d{4}$")                    # bare year "1919"


def _is_all_caps(s: str) -> bool:
    """Check if all letters in s are uppercase."""
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _is_poem_title(line: str) -> bool:
    """Check if a line is an ALL CAPS poem title (not header/boilerplate).

    Real poem titles start at column 0 (no leading whitespace). Indented
    ALL CAPS lines are epigraphs or dedications within poems.
    """
    # Must not be indented — poem titles start at column 0
    if line and line[0] == " ":
        return False
    # Must not start with parenthesis (subtitles like "(FADED WOMAN'S SONG)")
    if line and line[0] == "(":
        return False
    stripped = line.strip()
    if not stripped or len(stripped) < 2:
        return False
    # Must not have trailing page number (contents line)
    if CONTENTS_PAGE_RE.search(stripped):
        return False
    if STAR_LINE_RE.match(stripped):
        return False
    if PICTURE_RE.match(line):
        return False
    if DATE_LINE_RE.match(stripped) or DATE_RANGE_RE.match(stripped):
        return False
    if ROMAN_NUMERAL_RE.match(stripped):
        return False
    if YEAR_LINE_RE.match(stripped):
        return False
    if not _is_all_caps(stripped):
        return False
    # Strip trailing em-dash for comparison; normalize curly quotes
    norm = stripped.rstrip("—").strip()
    norm_straight = norm.replace("\u2019", "'").replace("\u2018", "'")
    if norm in SECTION_HEADERS or norm_straight in SECTION_HEADERS:
        return False
    if norm in SKIP_TITLES or norm_straight in SKIP_TITLES:
        return False
    # Skip boilerplate starting patterns
    if norm.startswith("MACMILLAN") or norm.startswith("ST. MARTIN"):
        return False
    return True


def _title_case(s: str) -> str:
    """Convert ALL CAPS title to title case, preserving quoted text."""
    # Handle titles that are entirely in quotes
    if s.startswith('"') and s.endswith('"'):
        inner = s[1:-1]
        return '"' + _title_case_words(inner) + '"'
    return _title_case_words(s)


def _title_case_words(s: str) -> str:
    """Title-case a string, handling small words and leading punctuation."""
    small_words = {
        "a", "an", "the", "and", "but", "or", "nor", "for", "yet", "so",
        "in", "on", "at", "to", "by", "of", "up", "as", "if", "is",
    }
    words = s.split()
    result = []
    for i, word in enumerate(words):
        # Strip punctuation to get the core word for small-word check
        core = word.lower().strip(".,;:!?()\"'\u201c\u201d\u2018\u2019")
        if i == 0 or core not in small_words:
            # Capitalize the first alphabetic character
            result.append(_capitalize_word(word))
        else:
            result.append(word.lower())
    return " ".join(result)


def _capitalize_word(word: str) -> str:
    """Capitalize a word, handling leading punctuation and em-dashes."""
    # Handle em-dash within word: "VATICAN—SALA" -> "Vatican—Sala"
    # But not standalone em-dashes
    if "—" in word and word != "—":
        parts = word.split("—")
        return "—".join(_capitalize_word(p) if p else "" for p in parts)
    # Keep abbreviations/initials with periods uppercase: "V.R.", "P.M."
    letters_only = re.sub(r"[^a-zA-Z]", "", word)
    if letters_only and all(
        c == "." or c.isalpha() for c in word.strip("\"'()")
    ) and "." in word and len(letters_only) <= 4:
        return word.upper()
    # Keep Roman numerals uppercase: "XXXI", "III", etc.
    # Use stricter validation to avoid false positives like "ILL"
    if (
        ROMAN_NUMERAL_RE.match(letters_only)
        and len(letters_only) >= 2
        and re.match(r"^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$", letters_only)
    ):
        return word.upper()
    lower = word.lower()
    chars = list(lower)
    for j, c in enumerate(chars):
        if c.isalpha():
            chars[j] = c.upper()
            break
    return "".join(chars)


def _clean_line(line: str) -> str:
    """Clean a verse line: strip whitespace, remove footnote markers."""
    line = line.rstrip()
    line = FOOTNOTE_MARKER_RE.sub("", line)
    return line.strip()


def _is_skip_line(line: str) -> bool:
    """Check if a line should be skipped entirely (not verse)."""
    stripped = line.strip()
    if not stripped:
        return False  # blank lines are meaningful
    if STAR_LINE_RE.match(stripped):
        return True
    if PICTURE_RE.match(line):
        return True
    if DATE_LINE_RE.match(stripped):
        return True
    if DATE_RANGE_RE.match(stripped):
        return True
    if YEAR_LINE_RE.match(stripped):
        return True
    # Italic date/place lines like "_September_ 1898."
    if stripped.startswith("_") and re.search(r"\d{4}", stripped):
        return True
    # Seasonal date attributions like "Summer: 1866."
    if re.match(r"^(Spring|Summer|Autumn|Winter|January|February|March|April|May|June|July|August|September|October|November|December)[,:]\s*\d{4}", stripped):
        return True
    # "T. H." signature
    if stripped == "T. H.":
        return True
    # "W. P. V." attributions (with optional leading number and date)
    if "W. P. V." in stripped:
        return True
    # Colophon/printer lines
    if "Printed in Great Britain" in stripped:
        return True
    # "SUNDAY NIGHT," and similar trailing date/place attributions
    if re.match(r"^[A-Z][A-Z ]+ NIGHT,$", stripped):
        return True
    # Place+date attributions: "MAX GATE, 1899." or "WESTBOURNE PARK VILLAS, 1866."
    # Also handles "PLYMOUTH (1914?)."
    if re.match(r"^[A-Z0-9][A-Z0-9 ,.'—()?]+\d{4}[?)]*\.$", stripped):
        return True
    # Place attributions ending with period: "MAX GATE."
    if re.match(r"^[A-Z][A-Z ]+\.$", stripped) and len(stripped) < 30:
        return True
    # Place attributions ending with comma: "WESTBOURNE PARK VILLAS,"
    if re.match(r"^[A-Z][A-Z ]+,$", stripped) and len(stripped) < 40:
        return True
    # Place+year attributions: "1867: WESTBOURNE PARK VILLAS." etc.
    if re.match(r"^\d{4}:", stripped):
        return True
    return False


def _extract_body(raw_text: str) -> list[str]:
    """Extract lines between Gutenberg START/END markers."""
    lines = raw_text.split("\n")
    start = 0
    end = len(lines)
    for i, line in enumerate(lines):
        if "*** START OF" in line:
            start = i + 1
        if "*** END OF" in line:
            end = i
            break
    return lines[start:end]


def _find_contents_end(lines: list[str]) -> int:
    """Find the line index where the CONTENTS section ends.

    The CONTENTS section has lines with trailing page numbers (4+ spaces
    then digits), section headers with trailing dashes, and TOC header
    lines with "PAGE". We find the last such line and skip past it.
    """
    last_contents_line = -1
    in_contents = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "CONTENTS":
            in_contents = True
            last_contents_line = i
            continue
        if in_contents:
            # Lines with trailing page numbers
            if CONTENTS_PAGE_RE.search(stripped):
                last_contents_line = i
            # TOC header lines ending with "PAGE"
            elif stripped.endswith("PAGE"):
                last_contents_line = i
            # Section headers ending with — (like "WAR POEMS—")
            elif stripped.endswith("—"):
                last_contents_line = i
            elif stripped:
                # ALL CAPS continuation lines right after a TOC line
                if _is_all_caps(stripped) and i - last_contents_line <= 2:
                    last_contents_line = i
                elif not _is_all_caps(stripped) and not stripped.startswith(" "):
                    # Non-caps, non-indented line = probably past contents
                    pass
                elif _is_all_caps(stripped):
                    # If we haven't seen a TOC line for a while, we're past contents
                    if i - last_contents_line > 5:
                        break

    if last_contents_line >= 0:
        return last_contents_line + 1
    return 0


def _find_poems_start(lines: list[str]) -> int:
    """Find where poems actually begin, after all front matter.

    Strategy: find the end of CONTENTS, then skip to the next ALL CAPS
    poem title that is followed by indented verse.
    """
    contents_end = _find_contents_end(lines)

    # Also skip past any preface/apology sections
    # These are identified by "PREFACE" or "APOLOGY" as ALL CAPS headers
    # followed by prose paragraphs (not indented verse)
    for i in range(contents_end, len(lines)):
        stripped = lines[i].strip()
        if stripped in ("PREFACE", "PREFACE TO WESSEX POEMS", "APOLOGY"):
            # Skip the preface block: find the next ALL CAPS title
            # that has indented verse after it
            continue

        if _is_poem_title(lines[i]):
            # Verify this is a real poem: look for indented verse within 6 lines
            for j in range(i + 1, min(i + 7, len(lines))):
                ahead = lines[j]
                if ahead.startswith("   ") and ahead.strip() and not PICTURE_RE.match(ahead):
                    return i
            # No indented verse found — might be a preface title, skip it

    # Fallback: just use after contents
    return contents_end


def _is_subtitle_line(line: str) -> bool:
    """Check if a line is a subtitle (not verse) following a title.

    Subtitles are:
      - Parenthetical: "(Southampton Docks: October, 1899)"
      - Dedication: "TO —"
      - Short descriptive: "A REVERIE"
      - Italic-marked: "_A Note of Reverie_"

    NOT verse lines (which typically start with spaces/indent or are
    sentences with mixed case).
    """
    stripped = line.strip()
    if not stripped:
        return False

    # Parenthetical subtitle (with or without italic markers)
    clean = stripped.replace("_", "")
    if clean.startswith("(") and clean.endswith(")"):
        return True

    # "TO —" dedication
    if stripped == "TO —" or stripped == "TO —.":
        return True

    # ALL CAPS subtitle like "A REVERIE" or longer subtitles
    # Must NOT be indented — indented ALL CAPS lines are epigraphs
    if _is_all_caps(stripped) and len(stripped) < 80 and not line.startswith(" "):
        norm = stripped.rstrip("—").strip()
        norm_straight = norm.replace("\u2019", "'").replace("\u2018", "'")
        if (
            norm not in SECTION_HEADERS
            and norm_straight not in SECTION_HEADERS
            and norm not in SKIP_TITLES
        ):
            if not ROMAN_NUMERAL_RE.match(stripped):
                return True

    # Italic subtitle: starts and ends with _
    if stripped.startswith("_") and stripped.endswith("_") and len(stripped) < 60:
        return True

    # Quoted epigraph/motto: "Secretum meum mihi"
    if stripped.startswith('"') and stripped.endswith('"') and len(stripped) < 60:
        return True

    return False


def _parse_file(path: Path, label: str) -> list[dict]:
    """Parse a single Gutenberg Hardy file into a list of poem dicts."""
    print(f"\n  Parsing {path.name} ({label})...")
    with open(path, encoding="utf-8") as f:
        raw = f.read()

    all_lines = _extract_body(raw)

    # Find where poems start
    poem_start = _find_poems_start(all_lines)
    print(f"    Body: {len(all_lines)} lines, poems start at line {poem_start}")

    # Find FOOTNOTES section
    footnotes_start = len(all_lines)
    for i, line in enumerate(all_lines):
        if line.strip() == "FOOTNOTES":
            footnotes_start = i
            break

    lines = all_lines[poem_start:footnotes_start]

    # Split into poem blocks by ALL CAPS title lines
    poems = []
    current_title = None
    current_subtitle = None
    current_lines = []

    def _flush():
        nonlocal current_title, current_subtitle, current_lines
        if current_title and current_lines:
            title = current_title
            if current_subtitle:
                title = f"{title} {current_subtitle}"
            poems.append((title, list(current_lines)))
        current_title = None
        current_subtitle = None
        current_lines = []

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if _is_skip_line(line):
            i += 1
            continue

        # Section header — skip (normalize curly quotes for comparison)
        norm = stripped.rstrip("—").strip()
        norm_straight = norm.replace("\u2019", "'").replace("\u2018", "'")
        if norm in SECTION_HEADERS or norm_straight in SECTION_HEADERS:
            i += 1
            continue

        # ALL CAPS poem title (must pass original line to check indentation)
        if _is_poem_title(line):
            _flush()
            # Clean footnote markers from title
            clean_stripped = FOOTNOTE_MARKER_RE.sub("", stripped).strip()
            current_title = _title_case(clean_stripped)

            # Look ahead for subtitle line(s)
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                next_stripped = next_line.strip()

                # Skip blanks and skip-lines between title and subtitle
                if not next_stripped or _is_skip_line(next_line):
                    j += 1
                    continue

                # Check for subtitle
                if _is_subtitle_line(next_line):
                    sub = next_stripped.replace("_", "").strip()
                    # Remove footnote markers from subtitle
                    sub = FOOTNOTE_MARKER_RE.sub("", sub).strip()
                    # Title-case ALL CAPS subtitles
                    if _is_all_caps(sub):
                        sub = _title_case(sub)
                    if current_subtitle:
                        current_subtitle += " " + sub
                    else:
                        current_subtitle = sub
                    j += 1
                    continue  # might be multiple subtitle lines

                # Not a subtitle — done looking
                break

            # Advance past subtitle lines
            i = j
            continue

        # Regular line — add to current poem
        if current_title is not None:
            current_lines.append(line)

        i += 1

    _flush()

    # Parse each poem block into stanzas
    result = []
    for title, poem_lines in poems:
        stanzas = _lines_to_stanzas(poem_lines)
        if stanzas:
            result.append({
                "title": title,
                "stanzas": stanzas,
            })

    print(f"    Found {len(result)} poems")
    return result


def _lines_to_stanzas(lines: list[str]) -> list[dict]:
    """Convert poem lines into stanza dicts.

    Roman numeral sub-sections (I, II, III) are treated as stanza breaks.
    Epigraph lines (indented, italic) are included in the first stanza.
    """
    stanzas = []
    current = []

    for line in lines:
        stripped = line.strip()

        if _is_skip_line(line):
            continue

        # Roman numeral on its own line = stanza break
        if ROMAN_NUMERAL_RE.match(stripped):
            if current:
                stanzas.append({"stanza_num": "", "lines": current})
                current = []
            continue

        # Blank line = stanza break
        if not stripped:
            if current:
                stanzas.append({"stanza_num": "", "lines": current})
                current = []
            continue

        # Section header somehow got through
        norm_s = stripped.rstrip("—").replace("\u2019", "'").replace("\u2018", "'")
        if norm_s in SECTION_HEADERS or stripped.rstrip("—") in SECTION_HEADERS:
            continue

        cleaned = _clean_line(line)
        if cleaned:
            current.append(cleaned)

    if current:
        stanzas.append({"stanza_num": "", "lines": current})

    return stanzas


def parse():
    all_poems = []
    seen_titles = set()
    total_lines = 0
    dupes = 0

    for filename, label in FILES:
        path = RAW_DIR / filename
        if not path.exists():
            print(f"  WARNING: {path} not found, skipping")
            continue

        poems = _parse_file(path, label)

        for poem in poems:
            norm_title = poem["title"].lower().strip()
            if norm_title in seen_titles:
                dupes += 1
                continue
            seen_titles.add(norm_title)

            n_lines = sum(len(s["lines"]) for s in poem["stanzas"])
            total_lines += n_lines
            all_poems.append(poem)

    print(f"\n  Total: {len(all_poems)} poems, {total_lines} lines")
    if dupes:
        print(f"  Deduplicated: {dupes} duplicate titles removed")

    # Print summary
    for poem in all_poems:
        n_st = len(poem["stanzas"])
        n_ln = sum(len(s["lines"]) for s in poem["stanzas"])
        print(f"    {poem['title']:60s} {n_st:3d} st  {n_ln:4d} ln")

    output = {
        "tcp_id": "",
        "gutenberg_id": "3167+3168+2997+4758",
        "author": "Thomas Hardy",
        "title": "Collected Poems (Wessex, Past and Present, Laughingstocks, Late Lyrics)",
        "date": "1928",
        "source": "Gutenberg",
        "poems": all_poems,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n  Written: {OUT_PATH}")


if __name__ == "__main__":
    parse()
