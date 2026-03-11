#!/usr/bin/env python3
"""
parse_delphi_poet.py — Parse a Delphi Poets Series epub (single-poet volume)
into intermediate JSON.

Unlike parse_delphi.py (for the multi-poet Anthology), this handles the
single-author Delphi Poets Series format:
  - h1 elements for major sections (Poetry Collections, Plays, Prose, etc.)
  - h1 with class h3/h5 for collection titles within Poetry Collections
  - h2 elements for individual poem titles (class h1 or h)
  - Verse in <p> with <br> line breaks, stanza breaks between <p> elements

Skips plays, prose, biographies, catalogues.

Usage:
    python scripts/parse_delphi_poet.py "Author Name" path/to/epub
    python scripts/parse_delphi_poet.py "W. B. Yeats" ~/Desktop/yeats.epub
    python scripts/parse_delphi_poet.py --all   # process all epubs in raw texts dir
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from html import unescape
from pathlib import Path

from lxml import html as lhtml

EBOOK_CONVERT = "/Applications/calibre.app/Contents/MacOS/ebook-convert"
INTERMEDIATE_DIR = Path("corpus/intermediate")

# Sections to SKIP (plays, prose, biographies, etc.)
SKIP_SECTIONS = {
    "The Plays", "The Play", "The Prose", "The Biographies",
    "The Autobiographies", "The Irish Dramatic Movement",
    "The Delphi Classics Catalogue",
    "List of Poems in Chronological Order",
    "List of Poems in Alphabetical Order",
    "List of Prose Works",
}

# h2 texts that are structural, not poem titles
STRUCTURAL_H2S = {
    "COPYRIGHT", "NOTE", "CONTENTS", "PREFACE", "INTRODUCTION",
    "INTRODUCTION.", "POEMS.", "PERSONS IN THE PLAY",
    "Series Contents",
}

# Poet-specific configuration
POET_CONFIG = {
    "W. B. Yeats": {
        "author": "W. B. Yeats",
        "date": "1919",  # approximate floruit
        "slug": "yeats",
        "skip_poems": {"The Second Coming"},  # bot rule: never quote this
    },
    "John Clare": {
        "author": "John Clare",
        "date": "1827",
        "slug": "clare",
    },
    "John Dryden": {
        "author": "John Dryden",
        "date": "1681",
        "slug": "dryden",
        # Skip translations section — we already have Dryden's Virgil from Delphi Anthology
        "skip_collections": {"Translations"},
    },
    "John Wilmot, Earl of Rochester": {
        "author": "John Wilmot, Earl of Rochester",
        "date": "1675",
        "slug": "rochester",
    },
}


def epub_to_html(epub_path: str) -> str:
    """Convert epub to HTML via Calibre."""
    with tempfile.TemporaryDirectory() as tmpdir:
        htmlz_path = os.path.join(tmpdir, "book.htmlz")
        result = subprocess.run(
            [EBOOK_CONVERT, epub_path, htmlz_path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"ebook-convert failed: {result.stderr}", file=sys.stderr)
            sys.exit(1)
        with zipfile.ZipFile(htmlz_path) as zf:
            return zf.read("index.html").decode("utf-8")


def extract_text(el) -> str:
    """Get all text from an element, stripping tags."""
    return unescape(el.text_content()).strip()


def extract_poem_lines(el) -> list[str]:
    """Extract lines of poetry from a <p> element. Lines separated by <br>."""
    # Remove line-number spans
    for span in el.findall(".//span"):
        cls = span.get("class", "")
        # Delphi uses various classes for line numbers
        if cls in ("t15", "t14", "t13", "t16"):
            span.text = ""
            span.tail = span.tail or ""

    raw = lhtml.tostring(el, encoding="unicode")
    raw = re.sub(r"^<p[^>]*>", "", raw)
    raw = re.sub(r"</p>$", "", raw)

    parts = re.split(r"<br\s*/?\s*>|<br\s+class=[^>]*>", raw)

    lines = []
    for part in parts:
        text = re.sub(r"<[^>]+>", "", part)
        text = unescape(text).rstrip()
        text = text.replace("\xa0", " ")
        if text.strip():
            lines.append(text)
    return lines


def is_skip_section(text: str) -> bool:
    """Check if an h1 text marks a section we should skip."""
    text = text.strip()
    for s in SKIP_SECTIONS:
        if s in text:
            return True
    return False


def is_structural(text: str) -> bool:
    """Check if an h2 text is structural (not a poem title)."""
    text = text.strip()
    if text in STRUCTURAL_H2S:
        return True
    # Roman numeral section markers within long poems
    if re.match(r"^[IVXLC]+\.?\s*$", text):
        return True
    # "SCENE I" etc. (plays that leaked in)
    if re.match(r"^SCENE\s", text):
        return True
    # "BOOK I" etc. within h2.h4 — these are sub-sections of contents
    if re.match(r"^BOOK\s", text):
        return True
    return False


def parse_poet_html(html_content: str, config: dict) -> list[dict]:
    """Parse the Delphi Poets Series HTML into a list of poems."""
    doc = lhtml.fromstring(html_content)

    skip_poems = config.get("skip_poems", set())
    skip_collections = config.get("skip_collections", set())

    # Walk through all elements to find poem divs
    # Structure: div.calibre contains either an h1 (section) or h2 (poem)
    divs = doc.findall(".//div[@class='calibre']")
    print(f"  Found {len(divs)} div sections")

    poems = []
    in_poetry_section = False
    in_skip_section = False
    current_collection = None

    for div in divs:
        # Check for h1 (major section headers / collection titles)
        h1s = div.findall(".//h1")
        h2s = div.findall(".//h2")

        if h1s:
            h1_text = extract_text(h1s[0])
            h1_class = h1s[0].get("class", "")

            # Top-level sections
            if h1_class == "h2":
                if "Poetry" in h1_text or "Poem" in h1_text:
                    in_poetry_section = True
                    in_skip_section = False
                    print(f"  Entering poetry section: {h1_text}")
                elif is_skip_section(h1_text):
                    in_poetry_section = False
                    in_skip_section = True
                    print(f"  Skipping section: {h1_text}")
                continue

            # Collection titles within poetry section
            if h1_class in ("h3", "h5") and in_poetry_section:
                current_collection = h1_text
                if current_collection in skip_collections:
                    print(f"  Skipping collection: {current_collection}")
                else:
                    print(f"    Collection: {current_collection}")
                continue

        if not in_poetry_section or in_skip_section:
            continue

        if current_collection in skip_collections:
            continue

        if not h2s:
            continue

        h2_text = extract_text(h2s[0])
        h2_class = h2s[0].get("class", "")

        # Skip structural h2s
        if is_structural(h2_text):
            continue

        # Skip h2.h4 — these are sub-contents or sub-sections, not poems
        # UNLESS they're in a collection where h4 IS the poem class (Clare early works)
        if h2_class == "h4":
            # In Clare's early works, h4 is used for some poems within collections
            # Check if there's actual verse content in this div
            paras = div.findall(".//p")
            has_verse = any(
                extract_poem_lines(p)
                for p in paras
                if p.get("class", "") not in ("p19", "p27")
                and extract_text(p).strip()
                and not extract_text(p).startswith("List of")
            )
            if not has_verse:
                continue

        # This should be a poem
        poem_title = h2_text.strip()

        # Clean title: remove trailing periods
        poem_title = re.sub(r"\.\s*$", "", poem_title)

        # Skip configured poems (case-insensitive)
        if any(poem_title.upper() == s.upper() for s in skip_poems):
            print(f"    SKIPPED (configured): {poem_title}")
            continue

        # Extract verse
        paras = div.findall(".//p")
        stanzas = []
        current_stanza = []

        for p in paras:
            p_text = extract_text(p)
            p_class = p.get("class", "")

            # Skip navigation, attribution, dates, images
            if "List of Poems" in p_text or "List of Poets" in p_text:
                continue
            if p.find(".//img") is not None and not p_text:
                continue
            if re.match(r"^\d{4}$", p_text.strip()):
                continue
            if not p_text.strip():
                continue

            lines = extract_poem_lines(p)
            if lines:
                # Each <p> is typically a stanza
                if current_stanza:
                    stanzas.append(current_stanza)
                current_stanza = lines

        if current_stanza:
            stanzas.append(current_stanza)

        if not stanzas:
            continue

        total_lines = sum(len(s) for s in stanzas)
        poems.append({
            "title": poem_title,
            "stanzas": [
                {"stanza_num": "", "lines": s}
                for s in stanzas
            ],
        })

    return poems


def parse_poet_html_flat(html_content: str, config: dict) -> list[dict]:
    """Parse Delphi volumes where ALL headings are h1 (no h1/h2 distinction).

    Used for Dryden and Rochester where the structure is flatter:
    h1.h1 for both section headers and poems, h2.h for poem titles.
    """
    doc = lhtml.fromstring(html_content)

    skip_poems = config.get("skip_poems", set())
    skip_collections = config.get("skip_collections", set())

    # In flat-structure volumes, h1s mark major sections and h2s mark poems
    h1s = doc.findall(".//h1")
    divs = doc.findall(".//div[@class='calibre']")
    print(f"  Found {len(divs)} div sections, {len(h1s)} h1s")

    poems = []
    in_poetry_section = False
    in_skip_section = False
    current_collection = None

    for div in divs:
        h1s_in_div = div.findall(".//h1")
        h2s_in_div = div.findall(".//h2")

        # Check for h1 section markers
        if h1s_in_div:
            h1_text = extract_text(h1s_in_div[0])
            h1_class = h1s_in_div[0].get("class", "")

            # Top-level sections
            if "Poetry Collection" in h1_text or h1_text == "The Poems":
                in_poetry_section = True
                in_skip_section = False
                print(f"  Entering poetry section: {h1_text}")
                continue
            if is_skip_section(h1_text):
                in_poetry_section = False
                in_skip_section = True
                print(f"  Skipping section: {h1_text}")
                continue

            # Collection headers (ALL CAPS h1s within poetry section)
            if in_poetry_section and h1_text.isupper() and len(h1_text) > 3:
                current_collection = h1_text
                if any(sc.upper() == current_collection for sc in skip_collections):
                    print(f"  Skipping collection: {current_collection}")
                    in_skip_section = True
                else:
                    in_skip_section = False
                    print(f"    Collection: {current_collection}")
                continue

        if not in_poetry_section or in_skip_section:
            continue

        if not h2s_in_div:
            continue

        h2_text = extract_text(h2s_in_div[0])
        h2_class = h2s_in_div[0].get("class", "")

        if is_structural(h2_text):
            continue

        poem_title = re.sub(r"\.\s*$", "", h2_text.strip())

        if poem_title in skip_poems:
            print(f"    SKIPPED: {poem_title}")
            continue

        # Extract verse
        paras = div.findall(".//p")
        stanzas = []
        current_stanza = []

        for p in paras:
            p_text = extract_text(p)
            if "List of Poems" in p_text or "List of Poets" in p_text:
                continue
            if p.find(".//img") is not None and not p_text:
                continue
            if re.match(r"^\d{4}$", p_text.strip()):
                continue
            if not p_text.strip():
                continue

            lines = extract_poem_lines(p)
            if lines:
                if current_stanza:
                    stanzas.append(current_stanza)
                current_stanza = lines

        if current_stanza:
            stanzas.append(current_stanza)

        if not stanzas:
            continue

        poems.append({
            "title": poem_title,
            "stanzas": [
                {"stanza_num": "", "lines": s}
                for s in stanzas
            ],
        })

    return poems


def detect_structure(html_content: str) -> str:
    """Detect whether the volume uses nested (Yeats/Clare) or flat (Dryden/Rochester) structure."""
    doc = lhtml.fromstring(html_content)
    h2s = doc.findall(".//h2")
    classes = set(el.get("class", "") for el in h2s)
    # If h2 elements have class "h1", it's nested (Yeats/Clare style)
    if "h1" in classes:
        return "nested"
    return "flat"


def write_output(poems: list[dict], config: dict):
    """Write intermediate JSON."""
    author = config["author"]
    slug = config["slug"]
    date = config["date"]

    total_lines = sum(
        sum(len(s["lines"]) for s in p["stanzas"])
        for p in poems
    )
    print(f"\n  {len(poems)} poems, {total_lines} lines")

    output = {
        "tcp_id": "",
        "gutenberg_id": "",
        "delphi_source": "Delphi Poets Series",
        "author": author,
        "title": f"Delphi Complete Poetical Works of {author}",
        "date": date,
        "source": "Delphi",
        "poems": poems,
    }

    out_path = INTERMEDIATE_DIR / f"DELPHI-POETS_{slug}.json"
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"  Written: {out_path}")

    # Show sample
    for p in poems[:10]:
        lines = sum(len(s["lines"]) for s in p["stanzas"])
        print(f"    {p['title'][:55]:55s} {lines:4d} lines")
    if len(poems) > 10:
        print(f"    ... and {len(poems) - 10} more")


def main():
    if len(sys.argv) < 3 and sys.argv[1:] != ["--all"]:
        print("Usage: python scripts/parse_delphi_poet.py 'Author Name' path/to/epub")
        print("       python scripts/parse_delphi_poet.py --all")
        sys.exit(1)

    if sys.argv[1] == "--all":
        raw_dir = Path(os.path.expanduser("~/Desktop/Canonbot raw texts"))
        for epub in sorted(raw_dir.glob("*.epub")):
            # Match author from filename: "Author Name - Delphi Poets Series.epub"
            name = epub.stem.split(" - ")[0].strip()
            if name not in POET_CONFIG:
                print(f"Skipping {epub.name} (no config for '{name}')")
                continue
            print(f"\n{'='*60}")
            print(f"Processing: {name}")
            print(f"{'='*60}")
            process_one(name, str(epub))
    else:
        author_name = sys.argv[1]
        epub_path = sys.argv[2]
        process_one(author_name, epub_path)


def process_one(author_name: str, epub_path: str):
    if author_name not in POET_CONFIG:
        print(f"Unknown poet: {author_name}. Add to POET_CONFIG.", file=sys.stderr)
        sys.exit(1)

    config = POET_CONFIG[author_name]

    print(f"  Converting {epub_path}...")
    html = epub_to_html(epub_path)
    print(f"  HTML: {len(html):,} bytes")

    structure = detect_structure(html)
    print(f"  Structure: {structure}")

    if structure == "nested":
        poems = parse_poet_html(html, config)
    else:
        poems = parse_poet_html_flat(html, config)

    write_output(poems, config)


if __name__ == "__main__":
    main()
