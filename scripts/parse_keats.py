#!/usr/bin/env python3
"""
parse_keats.py — Parse Keats from Gutenberg HTML into intermediate JSON.

Sources:
  - Gutenberg #23684: Poems Published in 1820 (well-structured HTML)
  - Gutenberg #8209:  Poems 1817 (poorly structured, font-tag HTML)

The 1820 volume is the essential one: all the great odes, Lamia, Isabella,
Eve of St. Agnes, Hyperion.

Usage:
    python scripts/parse_keats.py              # Fetch from Gutenberg if needed
    python scripts/parse_keats.py --cached     # Use already-downloaded HTML
"""

import json
import os
import re
import sys
import urllib.request
from html import unescape
from pathlib import Path

from lxml import html as lhtml

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GUTENBERG_FILES = {
    "keats_1820.html": "https://www.gutenberg.org/files/23684/23684-h/23684-h.htm",
    "keats_1817.html": "https://www.gutenberg.org/files/8209/8209-h/8209-h.htm",
}
RAW_DIR = Path("corpus/raw/gutenberg")
INTERMEDIATE_DIR = Path("corpus/intermediate")

POET = "John Keats"
DATE = "1820"

# Skip these h2 headings (not poems)
SKIP_HEADINGS = {
    "M. ROBERTSON",
    "PREFACE.",
    "CONTENTS",
    "LIFE OF KEATS",
    "OTHER POEMS.",
    "ADVERTISEMENT.",
    "POEMS.",
    "NOTES.",
    "TRANSCRIBER'S NOTES:",
}

# Multi-h2 poems: these headings append to an existing poem instead of creating new ones
APPEND_TO = {
    "BOOK I.": "Hyperion",
    "BOOK II.": "Hyperion",
    "BOOK III.": "Hyperion",
    "THE POT OF BASIL.": "Isabella; or, The Pot of Basil",
}

# Title normalization (h2 text → display title)
TITLE_MAP = {
    "LAMIA.": "Lamia",
    "ISABELLA;": "Isabella; or, The Pot of Basil",
    "EVE OF ST. AGNES.": "The Eve of St. Agnes",
    "ODE TO A NIGHTINGALE.": "Ode to a Nightingale",
    "ODE ON A GRECIAN URN.": "Ode on a Grecian Urn",
    "ODE TO PSYCHE.": "Ode to Psyche",
    "FANCY.": "Fancy",
    "ODE.": "Ode [Bards of Passion and of Mirth]",
    "LINES": "Lines on the Mermaid Tavern",
    "THE MERMAID TAVERN.": "Lines on the Mermaid Tavern",
    "ROBIN HOOD.": "Robin Hood",
    "TO AUTUMN.": "To Autumn",
    "ODE ON MELANCHOLY.": "Ode on Melancholy",
    "HYPERION.": "Hyperion",
}


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def ensure_downloaded():
    """Download Gutenberg HTML files if not cached."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in GUTENBERG_FILES.items():
        path = RAW_DIR / name
        if path.exists():
            print(f"  {name}: cached")
            continue
        print(f"  Fetching {name}...")
        req = urllib.request.Request(
            url, headers={"User-Agent": "CanonBot/1.0 (poetry corpus builder)"}
        )
        resp = urllib.request.urlopen(req)
        data = resp.read()
        path.write_bytes(data)
        print(f"    {len(data):,} bytes → {path}")


# ---------------------------------------------------------------------------
# 1820 Parser (well-structured HTML)
# ---------------------------------------------------------------------------


def extract_poem_lines_from_div(poem_div) -> list[list[str]]:
    """Extract stanzas from a .poem div.

    Returns list of stanzas, each a list of line strings.
    Skips stanza-number divs, page numbers, and line numbers.
    """
    stanzas = []
    for stanza_div in poem_div.findall(".//div[@class='stanza']"):
        lines = []
        # Remove linenum and pagenum spans before extracting text
        for junk in stanza_div.findall(".//span[@class='linenum']"):
            junk.getparent().remove(junk)
        for junk in stanza_div.findall(".//span[@class='pagenum']"):
            junk.getparent().remove(junk)

        for span in stanza_div.findall("./span"):
            cls = span.get("class", "")
            # Skip non-verse spans
            if cls in ("linenum", "pagenum", "ctr"):
                continue
            # Only take verse-line spans (i0, i1, i2, etc.)
            if not re.match(r"i\d", cls):
                continue
            text = span.text_content().strip()
            if text:
                text = text.replace("\xa0", " ")
                text = re.sub(r"\s+", " ", text).strip()
                # Skip bare stanza numbers ("1.", "2.", etc.)
                if re.match(r"^\d+\.\s*$", text):
                    continue
                lines.append(text)
        if lines:
            stanzas.append(lines)
    return stanzas


def parse_1820() -> list[dict]:
    """Parse the 1820 Gutenberg HTML into poems."""
    path = RAW_DIR / "keats_1820.html"
    with open(path, "rb") as f:
        doc = lhtml.fromstring(f.read())

    poems = []
    poems_by_title = {}  # title → poem dict, for appending sections

    # Walk through all h2 elements
    h2s = doc.findall(".//h2")
    for h2 in h2s:
        heading = h2.text_content().strip().replace("\n", " ")
        heading = re.sub(r"\s+", " ", heading)

        if heading in SKIP_HEADINGS:
            continue

        # Determine title: is this an append, a mapped title, or a new poem?
        append_target = APPEND_TO.get(heading)
        title = TITLE_MAP.get(heading)
        if title is None and heading not in TITLE_MAP and not append_target:
            title = heading.strip(".").title()

        # Collect all .poem divs that follow this h2 until the next h2
        poem_divs = []
        el = h2.getnext()
        while el is not None:
            if el.tag == "h2":
                break
            if el.tag == "div" and el.get("class") == "poem":
                poem_divs.append(el)
            for child in el.findall(".//div[@class='poem']"):
                if child not in poem_divs:
                    poem_divs.append(child)
            el = el.getnext()

        if not poem_divs:
            continue

        # Extract stanzas from all poem divs
        all_stanzas = []
        for pd in poem_divs:
            stanzas = extract_poem_lines_from_div(pd)
            all_stanzas.extend(stanzas)

        if not all_stanzas:
            continue

        # Append to existing poem?
        if append_target and append_target in poems_by_title:
            existing = poems_by_title[append_target]
            existing["stanzas"].append({
                "stanza_num": "",
                "lines": [f"[{heading.strip('.')}]"],
                "gaps": [],
            })
            for st in all_stanzas:
                existing["stanzas"].append({
                    "stanza_num": "", "lines": st, "gaps": []
                })
            continue

        # Use append_target as title if this is the first section
        if append_target:
            title = append_target

        if not title:
            continue

        # Build new poem
        poem = {
            "title": title,
            "stanzas": [
                {"stanza_num": "", "lines": st, "gaps": []}
                for st in all_stanzas
            ],
        }
        poems.append(poem)
        poems_by_title[title] = poem

    # Also grab specific Keats poems from LIFE/NOTES sections (which are
    # skipped above because they mix Keats with quotations from other poets)
    bonus_poems = _extract_bonus_poems(doc)
    for bp in bonus_poems:
        if bp["title"] not in poems_by_title:
            poems.append(bp)
            poems_by_title[bp["title"]] = bp

    return poems


# Keats poems embedded in LIFE/NOTES sections, identified by first-line fingerprint
_BONUS_FINGERPRINTS = {
    "On first looking into Chapman": "On First Looking into Chapman's Homer",
    "Bright star! would I were steadfast": "Bright Star",
    "La Belle Dame Sans Merci": "La Belle Dame Sans Merci",
}


def _extract_bonus_poems(doc) -> list[dict]:
    """Extract specific Keats poems from LIFE/NOTES sections by fingerprint."""
    results = []
    poem_divs = doc.findall(".//div[@class='poem']")

    for pd in poem_divs:
        # Get first line text
        first_text = ""
        for stanza in pd.findall(".//div[@class='stanza']"):
            for span in stanza.findall("./span"):
                cls = span.get("class", "")
                if cls.startswith("i"):
                    first_text = span.text_content().strip()
                    break
            if first_text:
                break

        # Check against fingerprints
        matched_title = None
        for fingerprint, title in _BONUS_FINGERPRINTS.items():
            if fingerprint in first_text:
                matched_title = title
                break

        if not matched_title:
            continue

        stanzas = extract_poem_lines_from_div(pd)
        if stanzas:
            # Strip title-echo stanzas (first stanza that's just the title)
            if stanzas[0] and len(stanzas[0]) == 1:
                first_line = stanzas[0][0]
                if any(fp in first_line for fp in _BONUS_FINGERPRINTS):
                    stanzas = stanzas[1:]
            if stanzas:
                results.append({
                    "title": matched_title,
                    "stanzas": [
                        {"stanza_num": "", "lines": st, "gaps": []}
                        for st in stanzas
                    ],
                })

    return results


# ---------------------------------------------------------------------------
# 1817 Parser (poorly structured HTML — font tags, br, nbsp)
# ---------------------------------------------------------------------------


def parse_1817() -> list[dict]:
    """Parse the 1817 Gutenberg HTML into poems.

    This is ugly HTML with <font> tags and &nbsp; indentation.
    Poems are separated by <hr> or by heading-like centered bold text.
    """
    path = RAW_DIR / "keats_1817.html"
    with open(path, "rb") as f:
        raw = f.read().decode("iso-8859-1")

    # Strip Gutenberg header/footer
    start_marker = "*** START OF"
    end_marker = "*** END OF"
    start = raw.find(start_marker)
    if start >= 0:
        start = raw.find("\n", start) + 1
    else:
        start = 0
    end = raw.find(end_marker)
    if end < 0:
        end = len(raw)
    raw = raw[start:end]

    doc = lhtml.fromstring(raw)

    poems = []
    # The 1817 volume uses <p><b><font>TITLE</font></b></p> for poem titles
    # and <p><font>verse lines with <br></font></p> for stanzas

    current_title = None
    current_stanzas = []

    for el in doc.iter():
        if el.tag == "hr":
            # HR often separates poems
            if current_title and current_stanzas:
                poems.append({
                    "title": current_title,
                    "stanzas": [
                        {"stanza_num": "", "lines": st, "gaps": []}
                        for st in current_stanzas
                    ],
                })
                current_title = None
                current_stanzas = []
            continue

        if el.tag != "p":
            continue

        text = el.text_content().strip()
        if not text:
            continue

        # Detect title paragraphs: short, mostly uppercase or bold
        b = el.find(".//b")
        is_bold = b is not None and b.text_content().strip() == text
        is_short = len(text) < 80
        is_upper = text == text.upper() and len(text) > 2

        if is_short and (is_bold or is_upper) and not any(c in text for c in ".,;:!?"):
            # Looks like a title
            if current_title and current_stanzas:
                poems.append({
                    "title": current_title,
                    "stanzas": [
                        {"stanza_num": "", "lines": st, "gaps": []}
                        for st in current_stanzas
                    ],
                })
            current_title = text.strip().title()
            current_stanzas = []
            continue

        # This is a stanza paragraph — extract lines split on <br>
        inner = lhtml.tostring(el, encoding="unicode")
        # Remove wrapping <p>
        inner = re.sub(r"^<p[^>]*>", "", inner)
        inner = re.sub(r"</p>$", "", inner)
        # Split on <br>
        parts = re.split(r"<br\s*/?\s*>", inner)
        lines = []
        for part in parts:
            line = re.sub(r"<[^>]+>", "", part)
            line = unescape(line)
            line = line.replace("\xa0", " ").rstrip()
            if line.strip():
                lines.append(line)
        if lines:
            current_stanzas.append(lines)

    if current_title and current_stanzas:
        poems.append({
            "title": current_title,
            "stanzas": [
                {"stanza_num": "", "lines": st, "gaps": []}
                for st in current_stanzas
            ],
        })

    # Filter out non-poem content (prefaces, notes, etc.)
    filtered = []
    skip_titles = {"contents", "preface", "dedication", "errata", "footnotes"}
    for p in poems:
        if p["title"].lower() in skip_titles:
            continue
        # Skip very short entries (likely headings captured as poems)
        total_lines = sum(len(s["lines"]) for s in p["stanzas"])
        if total_lines < 2:
            continue
        filtered.append(p)

    return filtered


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_intermediate(poems_1820: list[dict], poems_1817: list[dict]):
    """Write Keats intermediate JSON."""
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)

    # 1820 — the essential volume
    out_1820 = {
        "tcp_id": "",
        "gutenberg_id": "23684",
        "author": POET,
        "title": "Poems Published in 1820",
        "date": "1820",
        "source": "Gutenberg",
        "poems": poems_1820,
    }
    path_1820 = INTERMEDIATE_DIR / "GUT_keats-1820.json"
    with open(path_1820, "w") as f:
        json.dump(out_1820, f, indent=2, ensure_ascii=False)
    print(f"  {path_1820.name}: {len(poems_1820)} poems")

    # 1817 — early Keats
    if poems_1817:
        out_1817 = {
            "tcp_id": "",
            "gutenberg_id": "8209",
            "author": POET,
            "title": "Poems 1817",
            "date": "1817",
            "source": "Gutenberg",
            "poems": poems_1817,
        }
        path_1817 = INTERMEDIATE_DIR / "GUT_keats-1817.json"
        with open(path_1817, "w") as f:
            json.dump(out_1817, f, indent=2, ensure_ascii=False)
        print(f"  {path_1817.name}: {len(poems_1817)} poems")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    use_cached = "--cached" in sys.argv

    if not use_cached:
        print("Downloading from Gutenberg...")
        ensure_downloaded()
    else:
        print("Using cached HTML files")

    print("\nParsing 1820 volume...")
    poems_1820 = parse_1820()
    for p in poems_1820:
        n = sum(len(s["lines"]) for s in p["stanzas"])
        print(f"  {p['title']}: {len(p['stanzas'])} stanzas, {n} lines")

    print(f"\nParsing 1817 volume...")
    poems_1817 = parse_1817()
    for p in poems_1817:
        n = sum(len(s["lines"]) for s in p["stanzas"])
        print(f"  {p['title']}: {len(p['stanzas'])} stanzas, {n} lines")

    print(f"\nWriting intermediate JSON...")
    write_intermediate(poems_1820, poems_1817)

    total = len(poems_1820) + len(poems_1817)
    print(f"\nDone. {total} poems total.")
    print("Run chunk_corpus.py and ingest_to_chroma.py to embed.")


if __name__ == "__main__":
    main()
