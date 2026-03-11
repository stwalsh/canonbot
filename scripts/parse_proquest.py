#!/usr/bin/env python3
"""
parse_proquest.py — Parse ProQuest English Poetry database exports
into structured intermediate JSON (same format as parse_eebo_xml.py).

ProQuest exports are plain-text with a consistent structure:
  - Poems delimited by lines of underscores
  - Metadata header (Author, Title, Publication info, etc.)
  - "Full text:" marker starts the verse
  - Line numbers prefix each verse line
  - Roman numeral stanza markers (I, II, III... or i, ii, iii...)
  - [Page NNN] markers for page breaks
  - NOTES section with footnotes after the verse
  - Metadata block repeated after the verse (Title:, Pages:, etc.)

Usage:
    python scripts/parse_proquest.py                    # Parse all registered files
    python scripts/parse_proquest.py shelley            # Parse only Shelley files
"""

import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Registry — maps source keys to parse configs
# ---------------------------------------------------------------------------

REGISTRY = {
    "shelley_hutchinson_1": {
        "path": "corpus/raw/proquest/shelley_hutchinson_1.txt",
        "author": "Percy Bysshe Shelley",
        "work": "Complete Poetical Works (Hutchinson 1904)",
        "source": "ProQuest/Chadwyck-Healey",
    },
    "shelley_hutchinson_2": {
        "path": "corpus/raw/proquest/shelley_hutchinson_2.txt",
        "author": "Percy Bysshe Shelley",
        "work": "Complete Poetical Works (Hutchinson 1904)",
        "source": "ProQuest/Chadwyck-Healey",
    },
}

# ---------------------------------------------------------------------------
# Date lookup — Shelley's major works by composition/publication date
# ---------------------------------------------------------------------------

# Default date for Shelley; overridden per-poem where known
_SHELLEY_DEFAULT_DATE = "1820"

_SHELLEY_DATES = {
    "TO HARRIET": "1812",
    "TO MARY WOLLSTONECRAFT GODWIN": "1814",
    "STANZAS.—APRIL, 1814": "1814",
    "STANZA, WRITTEN AT BRACKNELL": "1814",
    "ALASTOR OR THE SPIRIT OF SOLITUDE": "1816",
    "MONT BLANC LINES WRITTEN IN THE VALE OF CHAMOUNI": "1816",
    "HYMN TO INTELLECTUAL BEAUTY": "1816",
    "A SUMMER EVENING CHURCHYARD LECHLADE, GLOUCESTERSHIRE": "1816",
    "FEELINGS OF A REPUBLICAN ON THE FALL OF BONAPARTE": "1816",
    "TO WORDSWORTH": "1816",
    "MUTABILITY": "1816",
    "ON DEATH": "1816",
    "A HATE-SONG": "1816",
    "THE DAEMON OF THE WORLD A FRAGMENT": "1816",
    "THE REVOLT OF ISLAM A POEM IN TWELVE CANTOS": "1818",
    "OZYMANDIAS": "1818",
    "LINES WRITTEN AMONG THE EUGANEAN HILLS": "1818",
    "JULIAN AND MADDALO A CONVERSATION": "1818",
    "ROSALIND AND HELEN A MODERN ECLOGUE": "1818",
    "STANZAS WRITTEN IN DEJECTION, NEAR NAPLES": "1818",
    "THE PAST": "1818",
    "INVOCATION TO MISERY": "1818",
    "SONNET [LIFT NOT THE PAINTED VEIL WHICH THOSE WHO LIVE ]": "1818",
    "PASSAGE OF THE APENNINES": "1818",
    "MARENGHI": "1818",
    "PRINCE ATHANASE A FRAGMENT": "1818",
    "PROMETHEUS UNBOUND A LYRICAL DRAMA IN FOUR ACTS": "1820",
    "THE CENCI A TRAGEDY IN FIVE ACTS": "1819",
    "THE MASK OF ANARCHY WRITTEN ON THE OCCASION OF THE MASSACRE AT MANCHESTER": "1819",
    "SONNET: ENGLAND IN 1819": "1819",
    "SONG TO THE MEN OF ENGLAND": "1819",
    "SIMILES FOR TWO POLITICAL CHARACTERS OF 1819": "1819",
    "LINES WRITTEN DURING THE CASTLEREAGH ADMINISTRATION": "1819",
    "AN ODE WRITTEN OCTOBER, 1819, BEFORE THE SPANIARDS HAD RECOVERED THEIR LIBERTY": "1819",
    "ODE TO THE WEST WIND": "1819",
    "THE INDIAN SERENADE": "1819",
    "PETER BELL THE THIRD BY MICHING MALLECHO, ESQ.": "1819",
    "OEDIPUS TYRANNUS OR SWELLFOOT THE TYRANT A TRAGEDY IN TWO ACTS TRANSLATED FROM THE ORIGINAL DORIC": "1820",
    "THE WITCH OF ATLAS": "1820",
    "ODE TO HEAVEN": "1820",
    "LETTER TO MARIA GISBORNE": "1820",
    "THE SENSITIVE PLANT": "1820",
    "ODE TO LIBERTY": "1820",
    "TO A SKYLARK": "1820",
    "THE CLOUD": "1820",
    "EPIPSYCHIDION VERSES ADDRESSED TO THE NOBLE AND UNFORTUNATE LADY, EMILIA V---, NOW IMPRISONED IN THE CONVENT OF ---": "1821",
    "ADONAIS AN ELEGY ON THE DEATH OF JOHN KEATS, AUTHOR OF ENDYMION, HYPERION,ETC.": "1821",
    "THE TRIUMPH OF LIFE": "1822",
    "ON THE MEDUSA OF LEONARDO DA VINCI IN THE FLORENTINE GALLERY": "1819",
    "FRAGMENT: TO BYRON": "1818",
    "SCENE FROM 'TASSO'": "1818",
    "SONG FOR 'TASSO'": "1818",
    "LOVE'S PHILOSOPHY": "1819",
    "ON A FADED VIOLET": "1818",
    "THE BIRTH OF PLEASURE": "1819",
    "AN EXHORTATION": "1819",
    "MARIANNE'S DREAM": "1818",
    "TO CONSTANTIA, SINGING": "1817",
    "TO CONSTANTIA": "1817",
    "THE SUNSET": "1816",
    "DEATH": "1817",
    "FRAGMENTS OF AN UNFINISHED DRAMA": "1822",
}


# ---------------------------------------------------------------------------
# Roman numeral detection
# ---------------------------------------------------------------------------

_ROMAN_RE = re.compile(
    r"^(M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))\s+(\d+)\s",
    re.IGNORECASE,
)


def _is_roman_stanza_marker(line: str) -> tuple[str, str] | None:
    """Check if a line starts with a Roman numeral stanza marker followed by a line number.

    Returns (roman, rest_of_line) or None.
    """
    m = _ROMAN_RE.match(line)
    if m:
        roman = m.group(1)
        # Sanity: reject single letters that are common English words
        if roman.upper() in ("I", "V", "X", "L", "C", "D", "M"):
            # For single "I", only accept if followed by a line number
            if roman.upper() == "I" and m.group(2):
                return roman, line[m.end():]
            # For others like V, X — check if they're clearly stanza markers
            # (i.e. the line number follows immediately)
            if m.group(2) and int(m.group(2)) > 1:
                return roman, line[m.end():]
            return None
        return roman, line[m.end():]
    return None


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------

_SEPARATOR_RE = re.compile(r"^_{10,}")
_PAGE_RE = re.compile(r"^\[Page \d+\]$")
_LINENUM_RE = re.compile(r"^(\d+)\s{2,}(.+)$")
_FULL_TEXT_RE = re.compile(r"^Full text:\s*")
_TITLE_RE = re.compile(r"^Title:\s*(.+)$")
_NOTES_RE = re.compile(r"^NOTES\s*$")
_FOOTNOTE_RE = re.compile(r"^\[\d+\]\s")


def _parse_single_export(path: str, config: dict) -> list[dict]:
    """Parse a single ProQuest export file into a list of poem dicts."""

    with open(path, encoding="utf-8") as f:
        raw = f.read()

    # Split on separator lines
    blocks = re.split(r"\n_{10,}\n", raw)

    poems = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        poem = _parse_block(block, config)
        if poem and poem["stanzas"]:
            poems.append(poem)

    return poems


def _parse_block(block: str, config: dict) -> dict | None:
    """Parse a single poem block (between separator lines)."""

    lines = block.split("\n")

    # Find the "Full text:" marker
    full_text_idx = None
    for i, line in enumerate(lines):
        if _FULL_TEXT_RE.match(line):
            full_text_idx = i
            break

    if full_text_idx is None:
        return None

    # Extract title from the metadata section after the verse
    title = None
    for line in lines:
        m = _TITLE_RE.match(line)
        if m:
            title = m.group(1).strip()
            break

    if not title:
        return None

    # Clean up the title
    display_title = _clean_title(title)

    # Get the verse lines (between "Full text:" and the post-verse metadata)
    verse_lines = _extract_verse_lines(lines, full_text_idx, title)

    if not verse_lines:
        return None

    # Parse verse into stanzas
    stanzas = _parse_stanzas(verse_lines)

    # Look up date
    date = _lookup_date(title, config)

    return {
        "title": display_title,
        "stanzas": stanzas,
    }


def _clean_title(title: str) -> str:
    """Clean and normalise a ProQuest poem title."""
    # Title case it (ProQuest titles are ALL CAPS)
    # But preserve certain words
    words = title.split()
    result = []
    small_words = {"A", "AN", "THE", "OF", "IN", "ON", "TO", "FOR", "AND", "OR",
                   "BUT", "NOR", "AT", "BY", "FROM", "WITH", "AS", "INTO", "NEAR"}

    for i, w in enumerate(words):
        if not w.isupper() and not w[0].isdigit():
            # Mixed case — leave as is
            result.append(w)
        elif w in ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
                    "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX"):
            result.append(w)
        elif i > 0 and w in small_words:
            result.append(w.lower())
        elif w.startswith("(") or w.startswith("["):
            result.append(w[0] + w[1:].title())
        else:
            result.append(w.title())

    out = " ".join(result)
    # Fix possessive 'S from title-casing (e.g. "Love'S" → "Love's")
    out = re.sub(r"([''])S\b", r"\1s", out)
    # Fix specific known title issues
    out = out.replace("HYPERION,Etc.", "Hyperion, etc.")
    return out


def _extract_verse_lines(lines: list[str], full_text_idx: int, raw_title: str = "") -> list[str]:
    """Extract the verse portion from a poem block.

    Starts after 'Full text:' line, ends before NOTES or the repeated Title: metadata.
    Strips [Page NNN] markers and title prefixes from lines.
    """
    verse = []

    # Build title prefix pattern to strip from the first verse line.
    # ProQuest format often puts "TITLE 1   first line text" on one line.
    title_prefix_re = None
    if raw_title:
        # Escape the title for regex and allow flexible whitespace
        escaped = re.escape(raw_title)
        title_prefix_re = re.compile(r"^" + escaped + r"\s+", re.IGNORECASE)

    for i in range(full_text_idx + 1, len(lines)):
        line = lines[i]
        stripped = line.strip()

        # Stop at NOTES section
        if _NOTES_RE.match(stripped):
            break

        # Stop at repeated metadata block
        if _TITLE_RE.match(stripped):
            break

        # Skip page markers
        if _PAGE_RE.match(stripped):
            continue

        # Skip footnotes that appear inline
        if _FOOTNOTE_RE.match(stripped):
            continue

        # Strip title prefix from first verse line
        if title_prefix_re and not verse:
            m = title_prefix_re.match(stripped)
            if m:
                stripped = stripped[m.end():]
                if stripped:
                    verse.append(stripped)
                continue
            # Also try: title appears alone on a line (preamble), skip it
            if stripped.upper() == raw_title.upper():
                continue
            # Multi-line titles: title split across lines before line numbers
            if stripped.upper() in raw_title.upper() and not _LINENUM_RE.match(stripped):
                continue

        # Strip title prefix even mid-poem (some re-state title after page breaks)
        if title_prefix_re:
            m = title_prefix_re.match(stripped)
            if m:
                rest = stripped[m.end():]
                if rest and _LINENUM_RE.match(rest):
                    verse.append(rest)
                    continue

        verse.append(stripped if not verse else line)

    return verse


def _parse_stanzas(verse_lines: list[str]) -> list[dict]:
    """Parse verse lines into stanzas.

    Stanza breaks are detected by:
    1. Roman numeral markers (I, II, III... prefixing a line number)
    2. Blank lines between numbered verse lines
    """
    stanzas = []
    current_lines = []
    current_stanza_num = ""
    has_roman_markers = False

    # First pass: check if this poem uses Roman numeral stanza markers
    for line in verse_lines:
        if _is_roman_stanza_marker(line.strip()):
            has_roman_markers = True
            break

    # Track whether we've seen any numbered verse lines yet
    seen_verse = False
    # Buffer for title/epigraph lines before the first numbered line
    preamble = []

    for line in verse_lines:
        stripped = line.strip()

        if not stripped:
            # Blank line = potential stanza break
            if current_lines and seen_verse:
                stanzas.append({
                    "stanza_num": current_stanza_num,
                    "lines": current_lines,
                })
                current_lines = []
                current_stanza_num = ""
            continue

        # Check for Roman numeral stanza marker
        roman_match = _is_roman_stanza_marker(stripped)
        if roman_match and has_roman_markers:
            roman, _ = roman_match
            # Flush current stanza
            if current_lines:
                stanzas.append({
                    "stanza_num": current_stanza_num,
                    "lines": current_lines,
                })
                current_lines = []
            current_stanza_num = roman
            # The line after the roman numeral has a line number — parse it
            # Re-parse the full stripped line without the roman prefix
            after_roman = _ROMAN_RE.sub("", stripped).strip()
            m = _LINENUM_RE.match(after_roman)
            if m:
                seen_verse = True
                current_lines.append(m.group(2))
            else:
                # Roman + line number on original line
                m = re.match(
                    r"^[IVXLCDM]+\s+\d+\s{2,}(.+)$", stripped, re.IGNORECASE
                )
                if m:
                    seen_verse = True
                    current_lines.append(m.group(1))
            continue

        # Check for numbered verse line
        m = _LINENUM_RE.match(stripped)
        if m:
            seen_verse = True
            current_lines.append(m.group(2))
            continue

        # Check for Roman + line number combined format: "XLVIII 424   text"
        m = re.match(r"^([IVXLCDM]+)\s+(\d+)\s{2,}(.+)$", stripped, re.IGNORECASE)
        if m:
            roman = m.group(1)
            if current_lines:
                stanzas.append({
                    "stanza_num": current_stanza_num,
                    "lines": current_lines,
                })
                current_lines = []
            current_stanza_num = roman
            seen_verse = True
            current_lines.append(m.group(3))
            continue

        # Non-numbered, non-blank line before any verse = preamble (skip)
        if not seen_verse:
            continue

        # Non-numbered line within verse — could be a continuation or stage direction
        # Include it as a line
        if current_lines or seen_verse:
            current_lines.append(stripped)

    # Flush last stanza
    if current_lines:
        stanzas.append({
            "stanza_num": current_stanza_num,
            "lines": current_lines,
        })

    return stanzas


def _lookup_date(title: str, config: dict) -> str:
    """Look up the date for a poem."""
    # Normalise for lookup
    key = title.upper().strip()
    if key in _SHELLEY_DATES:
        return _SHELLEY_DATES[key]

    # Try partial matches
    for k, v in _SHELLEY_DATES.items():
        if k in key or key in k:
            return v

    return _SHELLEY_DEFAULT_DATE


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    filter_key = sys.argv[1] if len(sys.argv) > 1 else None

    out_dir = Path("corpus/intermediate")
    out_dir.mkdir(parents=True, exist_ok=True)

    all_poems = []
    total_lines = 0

    for key, config in REGISTRY.items():
        if filter_key and filter_key not in key:
            continue

        path = config["path"]
        if not Path(path).exists():
            print(f"  SKIP {key}: {path} not found")
            continue

        print(f"  Parsing {key}...")
        poems = _parse_single_export(path, config)
        print(f"    {len(poems)} poems")

        for p in poems:
            n_lines = sum(len(s["lines"]) for s in p["stanzas"])
            total_lines += n_lines

        all_poems.extend(poems)

    if not all_poems:
        print("No poems parsed.")
        return

    # Deduplicate — some poems may appear in both exports
    seen_titles = set()
    deduped = []
    for p in all_poems:
        key = p["title"]
        if key in seen_titles:
            print(f"  DEDUP: skipping duplicate '{key}'")
            continue
        seen_titles.add(key)
        deduped.append(p)

    # Assign dates
    for p in deduped:
        raw_title = p["title"].upper()
        p_date = _lookup_date(raw_title, {})
        # Store date in poem dict for the chunk step to use
        # (intermediate format doesn't have per-poem date, but we'll add it)

    print(f"\n  Total: {len(deduped)} unique poems, {total_lines} lines")

    # Write intermediate JSON
    output = {
        "tcp_id": "",
        "gutenberg_id": "",
        "author": "Percy Bysshe Shelley",
        "title": "Complete Poetical Works (Hutchinson 1904)",
        "date": "1820",
        "source": "ProQuest/Chadwyck-Healey",
        "poems": deduped,
    }

    out_path = out_dir / "PQ_shelley-complete-poetical-works.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"  Written: {out_path}")


if __name__ == "__main__":
    main()
