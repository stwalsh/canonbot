#!/usr/bin/env python3
"""
parse_tennyson.py — Parse Tennyson from Gutenberg HTML into intermediate JSON.

Sources (up to and including Enoch Arden, 1864; excludes Idylls of the King):
  - #8601:  Early Poems (edited Collins) — Pattern A (p.noindent + br)
  - #70950: In Memoriam (1850) — Pattern C (div.poem/div.stanza/span.i*)
  - #56913: Maud, and Other Poems (1855) — Pattern B (pre)
  - #1358:  Enoch Arden, &c. (1864) — Pattern B (pre)
  - #791:   The Princess (1847) — Pattern B (pre)

Usage:
    python scripts/parse_tennyson.py              # Fetch if needed, parse all
    python scripts/parse_tennyson.py --cached     # Use cached HTML
"""

import json
import os
import re
import sys
import urllib.request
from html import unescape
from pathlib import Path

from lxml import html as lhtml

from gutenberg_utils import (
    clean_line,
    clean_title,
    extract_stanzas_noindent,
    extract_stanzas_pre,
    extract_stanzas_structured,
    lines_to_stanzas,
    stanzas_to_poem_dict,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GUTENBERG_FILES = {
    "tennyson_early.html": ("https://www.gutenberg.org/files/8601/8601-h/8601-h.htm", "8601"),
    "tennyson_in_memoriam.html": ("https://www.gutenberg.org/cache/epub/70950/pg70950-images.html", "70950"),
    "tennyson_maud.html": ("https://www.gutenberg.org/cache/epub/56913/pg56913-images.html", "56913"),
    "tennyson_enoch_arden.html": ("https://www.gutenberg.org/cache/epub/1358/pg1358-images.html", "1358"),
    "tennyson_princess.html": ("https://www.gutenberg.org/cache/epub/791/pg791-images.html", "791"),
}
RAW_DIR = Path("corpus/raw/gutenberg")
INTERMEDIATE_DIR = Path("corpus/intermediate")

POET = "Alfred, Lord Tennyson"

# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def ensure_downloaded():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name, (url, _) in GUTENBERG_FILES.items():
        path = RAW_DIR / name
        if path.exists():
            print(f"  {name}: cached")
            continue
        print(f"  Fetching {name}...")
        req = urllib.request.Request(
            url, headers={"User-Agent": "CanonBot/1.0 (poetry corpus)"}
        )
        resp = urllib.request.urlopen(req, timeout=30)
        data = resp.read()
        path.write_bytes(data)
        print(f"    {len(data):,} bytes")


# ---------------------------------------------------------------------------
# Early Poems (#8601) — Pattern A
# ---------------------------------------------------------------------------

EARLY_SKIP_H3 = {"I", "II", "III", "IV", "Appendix"}

EARLY_SKIP_TITLES = {
    "Table of Contents", "Preface", "Introduction",
    "Bibliography of the Poems of 1842", "Early Poems",
}


def parse_early() -> list[dict]:
    """Parse Early Poems (Collins edition). Pattern A: p.noindent + br."""
    path = RAW_DIR / "tennyson_early.html"
    doc = lhtml.fromstring(path.read_bytes())

    poems = []
    in_appendix = False

    for div in doc.findall(".//div[@class='chapter']"):
        h3 = div.find(".//h3")
        if h3 is None:
            continue

        title_raw = h3.text_content().strip()
        title = clean_title(title_raw)

        # Skip introduction sub-chapters and appendix
        if title in EARLY_SKIP_H3:
            if title == "Appendix":
                in_appendix = True
            continue
        if in_appendix:
            continue

        stanzas = extract_stanzas_noindent(div)
        if not stanzas:
            continue

        poems.append(stanzas_to_poem_dict(title, stanzas))

    return poems


# ---------------------------------------------------------------------------
# In Memoriam (#70950) — Pattern C
# ---------------------------------------------------------------------------


def parse_in_memoriam() -> list[dict]:
    """Parse In Memoriam. Pattern C: div.poem/div.stanza/span.i*."""
    path = RAW_DIR / "tennyson_in_memoriam.html"
    doc = lhtml.fromstring(path.read_bytes())

    # Remove pagenum spans globally
    for span in doc.findall(".//span[@class='pagenum']"):
        span.getparent().remove(span)

    # The whole poem is one work with numbered sections.
    # We'll keep it as one poem with all stanzas, but mark section boundaries.
    all_stanzas = []

    # Prologue: div.poetry before the first h3
    poetry_divs = doc.findall(".//div[@class='poetry']")
    h3s = doc.findall(".//h3")

    # Map h3 section numbers to their following poetry divs
    # First, grab the prologue (poetry div before any h3)
    prologue_done = False
    for pd in poetry_divs:
        poem_div = pd.find(".//div[@class='poem']")
        if poem_div is None:
            continue

        stanzas = extract_stanzas_structured(poem_div)
        if stanzas:
            all_stanzas.extend(stanzas)

    if not all_stanzas:
        return []

    return [stanzas_to_poem_dict("In Memoriam A.H.H.", all_stanzas)]


# ---------------------------------------------------------------------------
# Pre-based volumes (#56913 Maud, #1358 Enoch Arden, #791 Princess)
# ---------------------------------------------------------------------------

MAUD_SKIP = {
    "The Project Gutenberg eBook of Maud, and Other Poems",
    "By Alfred Tennyson",
    "THE FULL PROJECT GUTENBERG LICENSE",
}

ENOCH_SKIP = {
    "The Project Gutenberg eBook of Enoch Arden, &c.",
    "By Alfred Tennyson",
    "THE FULL PROJECT GUTENBERG LICENSE",
    "MISCELLANEOUS",
    "EXPERIMENTS",
    "IN QUANTITY",
}

PRINCESS_SKIP = {
    "The Project Gutenberg eBook of The Princess",
    "by Alfred Lord Tennyson",
    "Contents",
    "THE FULL PROJECT GUTENBERG LICENSE",
}

# Princess intercalary songs — pre blocks that are standalone lyrics
PRINCESS_SONGS = {
    "As through the land at eve we went": "As Through the Land at Eve We Went",
    "Sweet and low, sweet and low": "Sweet and Low",
    "The splendour falls on castle walls": "The Splendour Falls",
    "Tears, idle tears, I know not what they mean": "Tears, Idle Tears",
    "O Swallow, Swallow, flying, flying South": "O Swallow, Swallow",
    "Thy voice is heard thro' rolling drums": "Thy Voice Is Heard",
    "Home they brought her warrior dead": "Home They Brought Her Warrior Dead",
    "Our enemies have fall'n": "Our Enemies Have Fallen",
    "Ask me no more": "Ask Me No More",
    "Now sleeps the crimson petal, now the white": "Now Sleeps the Crimson Petal",
    "Come down, O maid, from yonder mountain height": "Come Down, O Maid",
}


def _parse_pre_volume(filename: str, skip_h2: set, is_princess: bool = False) -> list[dict]:
    """Parse a pre-based Tennyson volume.

    Walks h2 headings, collects <pre> blocks between headings as poem content.
    """
    path = RAW_DIR / filename
    doc = lhtml.fromstring(path.read_bytes())

    poems = []
    current_title = None
    current_stanzas = []
    maud_part = None

    # Remove boilerplate sections
    for bp in doc.findall(".//section[@class='pg-boilerplate pgheader']"):
        bp.getparent().remove(bp)

    # Walk body children sequentially
    body = doc.find(".//body")
    if body is None:
        return []

    def _flush():
        nonlocal current_title, current_stanzas
        if current_title and current_stanzas:
            poems.append(stanzas_to_poem_dict(current_title, current_stanzas))
        current_title = None
        current_stanzas = []

    for el in body.iter():
        if el.tag == "h2":
            text = clean_title(el.text_content())
            if text in skip_h2 or not text:
                continue

            _flush()
            current_title = text
            current_stanzas = []

            # Track Maud parts
            if text == "MAUD":
                maud_part = "I"

        elif el.tag == "pre":
            pre_text = el.text_content()

            # Check for Maud part markers
            if maud_part and "PART II" in pre_text[:50]:
                _flush()
                current_title = "MAUD"
                maud_part = "II"

            stanzas = extract_stanzas_pre(el)
            if not stanzas:
                continue

            # For Princess: check if this pre block is an intercalary song
            if is_princess and stanzas:
                first_line = stanzas[0][0].strip() if stanzas[0] else ""
                # Strip leading quotes (curly or straight) for matching
                first_clean = first_line.lstrip("'\u2018\u201c\"")
                for fingerprint, song_title in PRINCESS_SONGS.items():
                    if first_clean.startswith(fingerprint[:30]):
                        # Save current poem, emit song, resume
                        if current_title and current_stanzas:
                            poems.append(stanzas_to_poem_dict(current_title, current_stanzas))
                            saved_title = current_title
                            current_stanzas = []
                        else:
                            saved_title = current_title
                        poems.append(stanzas_to_poem_dict(song_title, stanzas))
                        current_title = saved_title
                        stanzas = None
                        break

                if stanzas is None:
                    continue

            # Strip section/part numbers from start of stanzas
            if stanzas:
                first = stanzas[0]
                while first and re.match(r"^(Part\s+)?\s*[IVXLC]+\.?\s*$", first[0].strip()):
                    first.pop(0)
                if not first:
                    stanzas.pop(0)

            current_stanzas.extend(stanzas)

    _flush()

    # Post-process: merge Maud parts into one poem
    maud_poems = [p for p in poems if p["title"] == "MAUD"]
    if len(maud_poems) > 1:
        # Keep first, append rest
        base = maud_poems[0]
        for mp in maud_poems[1:]:
            base["stanzas"].extend(mp["stanzas"])
            poems.remove(mp)
        base["title"] = "Maud"
    elif len(maud_poems) == 1:
        maud_poems[0]["title"] = "Maud"

    # Clean up titles
    for p in poems:
        t = p["title"]
        # Title-case ALL CAPS titles
        if t == t.upper() and len(t) > 3:
            # Smart title-casing that preserves "of", "the", etc.
            words = t.split()
            cased = []
            for i, w in enumerate(words):
                if i > 0 and w.lower() in ("of", "the", "a", "an", "and", "in", "on", "to", "at", "for"):
                    cased.append(w.lower())
                else:
                    cased.append(w.capitalize())
            p["title"] = " ".join(cased)

    return poems


def parse_maud() -> list[dict]:
    return _parse_pre_volume("tennyson_maud.html", MAUD_SKIP)


def parse_enoch_arden() -> list[dict]:
    return _parse_pre_volume("tennyson_enoch_arden.html", ENOCH_SKIP)


def parse_princess() -> list[dict]:
    return _parse_pre_volume("tennyson_princess.html", PRINCESS_SKIP, is_princess=True)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_intermediate(label: str, gut_id: str, date: str, poems: list[dict]):
    """Write one intermediate JSON file."""
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40]
    out = {
        "tcp_id": "",
        "gutenberg_id": gut_id,
        "author": POET,
        "title": label,
        "date": date,
        "source": "Gutenberg",
        "poems": poems,
    }
    path = INTERMEDIATE_DIR / f"GUT_tennyson-{slug}.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    total_lines = sum(
        sum(len(s["lines"]) for s in p["stanzas"])
        for p in poems
    )
    print(f"  {path.name}: {len(poems)} poems, {total_lines} lines")


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

    print("\n=== Early Poems (Collins, #8601) ===")
    early = parse_early()
    for p in early:
        n = sum(len(s["lines"]) for s in p["stanzas"])
        print(f"  {p['title']}: {len(p['stanzas'])} st, {n} ln")

    print(f"\n=== In Memoriam (#70950) ===")
    memoriam = parse_in_memoriam()
    for p in memoriam:
        n = sum(len(s["lines"]) for s in p["stanzas"])
        print(f"  {p['title']}: {len(p['stanzas'])} st, {n} ln")

    print(f"\n=== Maud and Other Poems (#56913) ===")
    maud = parse_maud()
    for p in maud:
        n = sum(len(s["lines"]) for s in p["stanzas"])
        print(f"  {p['title']}: {len(p['stanzas'])} st, {n} ln")

    print(f"\n=== The Princess (#791) ===")
    princess = parse_princess()
    for p in princess:
        n = sum(len(s["lines"]) for s in p["stanzas"])
        print(f"  {p['title']}: {len(p['stanzas'])} st, {n} ln")

    print(f"\n=== Enoch Arden &c. (#1358) ===")
    enoch = parse_enoch_arden()
    for p in enoch:
        n = sum(len(s["lines"]) for s in p["stanzas"])
        print(f"  {p['title']}: {len(p['stanzas'])} st, {n} ln")

    print(f"\nWriting intermediate JSON...")
    write_intermediate("Early Poems", "8601", "1842", early)
    write_intermediate("In Memoriam", "70950", "1850", memoriam)
    write_intermediate("Maud and Other Poems", "56913", "1855", maud)
    write_intermediate("The Princess", "791", "1847", princess)
    write_intermediate("Enoch Arden", "1358", "1864", enoch)

    total = len(early) + len(memoriam) + len(maud) + len(princess) + len(enoch)
    print(f"\nDone. {total} poems total across 5 volumes.")
    print("Run chunk_corpus.py and ingest_to_chroma.py to embed.")


if __name__ == "__main__":
    main()
