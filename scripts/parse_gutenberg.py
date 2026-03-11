#!/usr/bin/env python3
"""
parse_gutenberg.py — Parse Project Gutenberg HTML and plain-text poetry sources
into structured intermediate JSON (same format as parse_eebo_xml.py).

Registry pattern: each Gutenberg text has a config entry specifying format,
URL, author, etc. Adding a new text = adding a registry entry.

Usage:
    python scripts/parse_gutenberg.py                  # Parse all registered texts
    python scripts/parse_gutenberg.py 66619            # Parse a single Gutenberg ID
"""

import json
import os
import re
import sys
from pathlib import Path

import requests
import yaml
from lxml import html as lxml_html

# ---------------------------------------------------------------------------
# Registry — maps Gutenberg IDs to parser configs
# ---------------------------------------------------------------------------

REGISTRY = {
    "66619": {
        "format": "html",
        "type": "anthology",
        "url": "https://www.gutenberg.org/cache/epub/66619/pg66619-images.html",
        "title": "The Oxford Book of English Verse, 1250-1900",
        "filename": "pg66619-images.html",
    },
    "703": {
        "format": "plaintext",
        "type": "single_author",
        "url": "https://www.gutenberg.org/cache/epub/703/pg703.txt",
        "title": "Lucasta",
        "author": "Richard Lovelace",
        "date": "1649",
        "filename": "pg703.txt",
    },
    "pope": {
        "format": "couplets_txt",
        "type": "single_author",
        "path": "corpus/raw/couplets.txt",
        "title": "Poetical Works (non-Homer)",
        "author": "Alexander Pope",
        "date": "1734",
    },
}


def load_config(config_path: str = "config/config.yaml") -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def download_or_cache(gutenberg_id: str, cache_dir: str) -> str:
    """Download a Gutenberg text if not already cached. Returns local path."""
    entry = REGISTRY[gutenberg_id]
    local_path = os.path.join(cache_dir, entry["filename"])

    if os.path.exists(local_path):
        print(f"  Using cached {local_path}")
        return local_path

    print(f"  Downloading {entry['url']} ...")
    os.makedirs(cache_dir, exist_ok=True)
    resp = requests.get(entry["url"], timeout=120)
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        f.write(resp.content)
    print(f"  Saved to {local_path}")
    return local_path


# ---------------------------------------------------------------------------
# OBEV HTML parser (anthology: one intermediate JSON per poet)
# ---------------------------------------------------------------------------

def parse_obev_html(html_path: str) -> list[dict]:
    """
    Parse the Oxford Book of English Verse HTML into per-poet intermediate JSON dicts.
    Returns a list of dicts, each representing one poet's poems.
    """
    with open(html_path, "rb") as f:
        tree = lxml_html.parse(f)
    root = tree.getroot()

    # Find all h2 and h3 elements in document order, plus poem/stanza divs
    # Strategy: walk the body, tracking current poet, building poems
    body = root.find(".//body")
    if body is None:
        print("  ERROR: No body element found")
        return []

    poets = []
    current_poet = None
    current_poet_dates = ""
    current_poems = []
    in_contents = False

    # Iterate through all elements in document order
    for elem in body.iter():
        tag = elem.tag

        # Skip contents section
        if tag == "h2":
            text = (elem.text_content() or "").strip()
            if text == "CONTENTS":
                in_contents = True
                continue
            if in_contents and text not in ("CONTENTS",):
                # First h2 after contents = back to poems
                in_contents = False

            # Skip non-poet h2s
            if text in ("PREFACE", "CONTENTS", "INDEX OF WRITERS",
                        "INDEX OF FIRST LINES", "NOTES", "GLOSSARY",
                        "Transcriber's Note", ""):
                continue
            if text.startswith("The Project Gutenberg"):
                continue

            # Save previous poet
            if current_poet and current_poems:
                poets.append(_build_poet_record(
                    current_poet, current_poet_dates, current_poems, "66619"
                ))

            current_poet = _normalize_obev_name(text)
            current_poet_dates = ""
            current_poems = []

            # Look for dates in next sibling (p.dtt)
            next_el = elem.getnext()
            if next_el is not None:
                cls = next_el.get("class", "")
                if "dtt" in cls:
                    current_poet_dates = (next_el.text_content() or "").strip()

        elif tag == "h3" and not in_contents:
            # Poem title
            title = (elem.text_content() or "").strip()
            if title and current_poet:
                current_poems.append({
                    "title": title,
                    "stanzas": [],
                    "_pending": True,
                })

        elif tag == "div" and not in_contents:
            cls = elem.get("class", "")
            if cls == "stanza" and current_poems:
                # Extract lines from this stanza
                lines = _extract_lines_from_stanza(elem)
                if lines:
                    stanza = {
                        "stanza_num": "",
                        "lines": lines,
                        "gaps": [],
                    }
                    # Add to most recent poem
                    current_poems[-1]["stanzas"].append(stanza)
                    current_poems[-1].pop("_pending", None)

    # Save final poet
    if current_poet and current_poems:
        poets.append(_build_poet_record(
            current_poet, current_poet_dates, current_poems, "66619"
        ))

    return poets


def _extract_lines_from_stanza(stanza_div) -> list[str]:
    """Extract lines from a stanza div. Lines are in <span class="i0"> etc."""
    lines = []
    for span in stanza_div.findall(".//span"):
        cls = span.get("class", "")
        if cls.startswith("i") and cls[1:].isdigit():
            text = span.text_content().strip()
            # Remove trailing <br> artifacts
            text = text.rstrip()
            if text:
                lines.append(text)
    return lines


def _normalize_obev_name(raw: str) -> str:
    """Normalize OBEV poet names from 'JOHN KEATS' to 'John Keats'."""
    raw = raw.strip()
    # Remove anchor text artifacts
    raw = re.sub(r"\s+", " ", raw)

    # Handle special cases
    if raw.upper().startswith("ANONYMOUS"):
        return "Anonymous"

    # Title case, but preserve particles
    parts = raw.split()
    result = []
    lowercase_words = {"of", "de", "the", "and", "or", "van", "von"}
    for i, p in enumerate(parts):
        clean = p.strip(",").strip(".")
        if clean.lower() in lowercase_words and i > 0:
            result.append(clean.lower())
        else:
            result.append(clean.capitalize())
    return " ".join(result)


def _parse_obev_dates(date_str: str) -> str:
    """Extract a usable date from OBEV date strings like '1795-1821' or 'd. 1395'."""
    if not date_str:
        return ""
    # Try to find a 4-digit year
    # For ranges like '1795-1821', use the first (birth) year as approximate
    # For publication dating, we'll use individual poem dates where available
    match = re.search(r"(\d{4})", date_str)
    return match.group(1) if match else ""


def _build_poet_record(poet: str, dates: str, poems: list[dict], gut_id: str) -> dict:
    """Build an intermediate JSON record for one poet from OBEV."""
    # Filter out poems with no stanzas
    valid_poems = [p for p in poems if p.get("stanzas")]
    for p in valid_poems:
        p.pop("_pending", None)

    # Try to extract a date — for OBEV, use the poet's dates as a rough estimate
    # We'll use the later date (death year or second year in range) as approximate pub date
    date = ""
    if dates:
        years = re.findall(r"\d{4}", dates)
        if len(years) >= 2:
            # Use average of birth/death as rough floruit
            birth, death = int(years[0]), int(years[1])
            date = str((birth + death) // 2)
        elif years:
            date = years[0]

    return {
        "tcp_id": "",
        "gutenberg_id": gut_id,
        "author": poet,
        "title": f"The Oxford Book of English Verse",
        "date": date,
        "source": "Gutenberg",
        "poems": valid_poems,
        "gap_log": [],
    }


# ---------------------------------------------------------------------------
# Lucasta / single-author plaintext parser
# ---------------------------------------------------------------------------

def parse_lucasta_plaintext(text_path: str) -> dict:
    """Parse Lucasta plain text into intermediate JSON."""
    with open(text_path, encoding="utf-8") as f:
        raw = f.read()

    # Strip Gutenberg boilerplate
    start_marker = "*** START OF THE PROJECT GUTENBERG EBOOK"
    end_marker = "*** END OF THE PROJECT GUTENBERG EBOOK"

    start_idx = raw.find(start_marker)
    if start_idx >= 0:
        raw = raw[raw.index("\n", start_idx) + 1:]
    end_idx = raw.find(end_marker)
    if end_idx >= 0:
        raw = raw[:end_idx]

    # Strip all footnotes first: they start with <N.N> and run until the next
    # triple-blank-line or next poem title. We remove everything from <N.N> to
    # the next blank line that's followed by a non-footnote line.
    raw = _strip_footnotes(raw)

    lines = raw.split("\n")

    # Find where the actual poems begin (after "POEMS." header)
    poems_start = _find_poems_start(lines)

    # Stop before elegies/translations (these aren't Lovelace's own poems)
    poems_end = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped in ("ELEGIES ON THE DEATH OF THE AUTHOR.",
                        "TRANSLATIONS.", "ELEGIES."):
            poems_end = i
            break

    poems = _extract_plaintext_poems(lines[poems_start:poems_end])

    return {
        "tcp_id": "",
        "gutenberg_id": "703",
        "author": "Richard Lovelace",
        "title": "Lucasta",
        "date": "1649",
        "source": "Gutenberg",
        "poems": poems,
        "gap_log": [],
    }


def _strip_footnotes(text: str) -> str:
    """Remove Gutenberg editorial footnotes from text.

    Footnotes start with <N.N> (e.g. <17.1>, <2.22>) and continue until
    a sequence of 2+ blank lines (which marks the next poem).
    """
    lines = text.split("\n")
    result = []
    in_footnote = False

    for line in lines:
        stripped = line.strip()

        # Detect footnote start: line begins with <digits.digits>
        if re.match(r"^<\d+\.\d+>", stripped):
            in_footnote = True
            continue

        # Also catch inline footnote markers without being the start of a footnote block
        if in_footnote:
            if stripped == "":
                # Blank lines in footnotes — check if we've hit the end
                # (multiple blank lines signal return to poem text)
                result.append("")  # preserve blank line for structure
                continue
            # If we see a centered ALL-CAPS line, the footnote is over
            leading = len(line) - len(line.lstrip())
            alpha = [c for c in stripped if c.isalpha()]
            if alpha and leading >= 10:
                upper_ratio = sum(1 for c in alpha if c.isupper()) / len(alpha)
                if upper_ratio > 0.6:
                    in_footnote = False
                    result.append(line)
                    continue
            # Still in footnote — skip
            continue

        # Remove inline footnote references like <16.1>
        cleaned = re.sub(r"<\d+\.\d+>", "", line)
        result.append(cleaned)

    return "\n".join(result)


def _find_poems_start(lines: list[str]) -> int:
    """Find the line index where actual poems begin in Lucasta."""
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "POEMS.":
            return i + 1
    # Fallback: look for first "SONG." after line 200
    for i, line in enumerate(lines):
        if i < 200:
            continue
        stripped = line.strip()
        if stripped.startswith("SONG.") or stripped.startswith("ODE."):
            return i
    return 0


def _extract_plaintext_poems(lines: list[str]) -> list[dict]:
    """
    Extract poems from Lucasta plaintext. Structure:
    - Title lines are ALL CAPS, centered, sometimes multi-line
    - Stanza numbers like "I.", "II.", etc. (Roman numerals, centered)
    - Poem lines are indented text
    - Stanzas separated by blank lines
    """
    poems = []
    current_title_lines = []
    current_stanzas = []
    current_stanza_lines = []
    in_poem = False
    blank_count = 0

    for line in lines:
        stripped = line.strip()

        # Blank line handling
        if not stripped:
            blank_count += 1
            if in_poem and current_stanza_lines:
                # End of stanza on first blank
                current_stanzas.append({
                    "stanza_num": "",
                    "lines": current_stanza_lines,
                    "gaps": [],
                })
                current_stanza_lines = []
            continue

        prev_blank_count = blank_count
        blank_count = 0

        # Roman numeral stanza markers (centered)
        if re.match(r"^\s{5,}(I{1,4}V?|VI{0,4}|IX|X{1,3}I{0,3}V?|XV?I{0,4}|XX?I{0,3}V?)\.\s*$", line):
            # Stanza number marker — flush current stanza if any
            if current_stanza_lines:
                current_stanzas.append({
                    "stanza_num": "",
                    "lines": current_stanza_lines,
                    "gaps": [],
                })
                current_stanza_lines = []
            continue

        # Detect title lines: centered (leading whitespace >= 10) and mostly uppercase
        is_title = _is_title_line(line, stripped)

        # "SET BY" lines — skip these (musical attribution, not poem content)
        if stripped.startswith("SET BY"):
            continue

        if is_title:
            if in_poem or (prev_blank_count >= 2 and current_title_lines):
                # New poem — flush previous
                if current_stanza_lines:
                    current_stanzas.append({
                        "stanza_num": "",
                        "lines": current_stanza_lines,
                        "gaps": [],
                    })
                    current_stanza_lines = []
                if current_title_lines and current_stanzas:
                    title = _clean_title(" ".join(current_title_lines))
                    poems.append({"title": title, "stanzas": current_stanzas})
                current_title_lines = [stripped]
                current_stanzas = []
                in_poem = False
            else:
                current_title_lines.append(stripped)
            continue

        # Regular poem line
        if current_title_lines and not in_poem:
            in_poem = True

        if in_poem:
            cleaned = stripped
            if cleaned:
                current_stanza_lines.append(cleaned)

    # Flush final poem
    if current_stanza_lines:
        current_stanzas.append({
            "stanza_num": "",
            "lines": current_stanza_lines,
            "gaps": [],
        })
    if current_title_lines and current_stanzas:
        title = _clean_title(" ".join(current_title_lines))
        poems.append({"title": title, "stanzas": current_stanzas})

    return poems


def _is_title_line(raw_line: str, stripped: str) -> bool:
    """Heuristic: is this line a poem title?"""
    if not stripped:
        return False
    # Titles are typically centered (lots of leading whitespace) and mostly uppercase
    leading_spaces = len(raw_line) - len(raw_line.lstrip())
    if leading_spaces < 10:
        return False

    # Check if mostly uppercase (allow for articles, prepositions)
    alpha_chars = [c for c in stripped if c.isalpha()]
    if not alpha_chars:
        return False
    upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)

    # Titles are mostly uppercase
    if upper_ratio > 0.6:
        return True

    return False


def _clean_title(title: str) -> str:
    """Clean up a Lucasta poem title."""
    # Remove footnote markers
    title = re.sub(r"<\d+\.\d+>", "", title)
    # Collapse whitespace
    title = re.sub(r"\s+", " ", title).strip()
    # Remove trailing period if it's just punctuation
    title = title.rstrip(".")
    return title


# ---------------------------------------------------------------------------
# Pope couplets parser
# ---------------------------------------------------------------------------

def parse_pope_couplets(text_path: str) -> dict:
    """Parse Pope couplets file (lines separated by '---') into intermediate JSON."""
    with open(text_path, encoding="utf-8") as f:
        raw = f.read()

    # Split on --- delimiters
    blocks = re.split(r"^---\s*$", raw, flags=re.MULTILINE)

    stanzas = []
    for block in blocks:
        lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
        if lines:
            stanzas.append({
                "stanza_num": "",
                "lines": lines,
                "gaps": [],
            })

    # Single poem containing all couplets as stanzas
    poems = [{
        "title": "Poetical Works (non-Homer)",
        "stanzas": stanzas,
    }]

    return {
        "tcp_id": "",
        "gutenberg_id": "",
        "author": "Alexander Pope",
        "title": "Poetical Works (non-Homer)",
        "date": "1734",
        "source": "Pope couplets (popebot)",
        "poems": poems,
        "gap_log": [],
    }


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def parse_gutenberg(gutenberg_id: str, cache_dir: str, output_dir: str) -> list[str]:
    """Parse a single Gutenberg source. Returns list of output file paths."""
    if gutenberg_id not in REGISTRY:
        print(f"  Unknown Gutenberg ID: {gutenberg_id}")
        return []

    entry = REGISTRY[gutenberg_id]
    os.makedirs(output_dir, exist_ok=True)

    output_files = []

    if entry["format"] == "couplets_txt":
        # Local file, no download needed
        local_path = entry["path"]
        record = parse_pope_couplets(local_path)
        filename = f"POPE_couplets.json"
        out_path = os.path.join(output_dir, filename)
        with open(out_path, "w") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
        output_files.append(out_path)
        stanza_count = len(record["poems"][0]["stanzas"])
        print(f"  {record['author']}: {stanza_count} couplets → {filename}")
        return output_files

    local_path = download_or_cache(gutenberg_id, cache_dir)

    if entry["format"] == "html" and entry["type"] == "anthology":
        poet_records = parse_obev_html(local_path)
        print(f"  Parsed {len(poet_records)} poets from {entry['title']}")

        for record in poet_records:
            if not record["poems"]:
                continue
            # One file per poet
            poet_slug = _slugify(record["author"])
            filename = f"GUT{gutenberg_id}_{poet_slug}.json"
            out_path = os.path.join(output_dir, filename)
            with open(out_path, "w") as f:
                json.dump(record, f, indent=2, ensure_ascii=False)
            output_files.append(out_path)
            poem_count = len(record["poems"])
            print(f"    {record['author']}: {poem_count} poems → {filename}")

    elif entry["format"] == "plaintext" and entry["type"] == "single_author":
        record = parse_lucasta_plaintext(local_path)
        filename = f"GUT{gutenberg_id}.json"
        out_path = os.path.join(output_dir, filename)
        with open(out_path, "w") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
        output_files.append(out_path)
        print(f"  {record['author']}: {len(record['poems'])} poems → {filename}")

    return output_files


def _slugify(text: str) -> str:
    """Simple slug for filenames."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:40].rstrip("-")


def main():
    config = load_config()
    cache_dir = config.get("gutenberg", {}).get("cache_dir", "corpus/raw/gutenberg")
    output_dir = config["paths"]["intermediate"]
    registry_ids = config.get("gutenberg", {}).get("registry_ids", [])

    if len(sys.argv) > 1:
        # Parse specific ID(s)
        ids = sys.argv[1:]
    else:
        ids = [str(rid) for rid in registry_ids]

    if not ids:
        print("No Gutenberg IDs to parse. Check config/config.yaml gutenberg.registry_ids")
        return

    total_files = 0
    for gid in ids:
        print(f"\n--- Gutenberg #{gid} ---")
        files = parse_gutenberg(gid, cache_dir, output_dir)
        total_files += len(files)

    print(f"\nTotal: {total_files} intermediate JSON files written")


if __name__ == "__main__":
    main()
