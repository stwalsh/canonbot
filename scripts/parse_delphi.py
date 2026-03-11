#!/usr/bin/env python3
"""
parse_delphi.py — Parse Delphi Poetry Anthology HTML (from epub via Calibre)
into structured intermediate JSON matching the existing corpus format.

Converts epub → HTML via ebook-convert, then parses poet sections and poems.

Filters:
  - Keeps all native English poets (Chaucer onward)
  - Keeps Pope/Cowper Homer, Dryden Virgil (Aeneid), Marlowe Ovid
  - Skips Sappho, Conington Horace, Greenough Virgil, Dante, other translations

Usage:
    python scripts/parse_delphi.py path/to/anthology.epub
    python scripts/parse_delphi.py  # defaults to ~/Desktop/Delphi Poetry Anthology.epub
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

from lxml import html as lxml_html

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

EBOOK_CONVERT = "/Applications/calibre.app/Contents/MacOS/ebook-convert"

# Poets whose sections are translations — only keep specific translators
TRANSLATED_POETS = {
    "Homer": {
        "keep_translators": {"Alexander Pope", "William Cowper"},
        "attribution_poet": None,  # use translator name
    },
    "Virgil": {
        "keep_translators": {"John Dryden"},
        "attribution_poet": None,
    },
    "Ovid": {
        "keep_translators": {"Christopher Marlowe"},
        "attribution_poet": None,
    },
}

# Skip these poet sections entirely
SKIP_POETS = {"Sappho", "Horace", "Dante Alighieri"}

# Section headings (not poet names, not poem titles)
SECTION_HEADINGS = {
    "The Ancients",
    "Medieval Poetry",
    "Renaissance Poets",
    "Restoration and Eighteenth Century Poets",
    "Early Nineteenth Century Poets",
    "Victorian Era Poets",
    "Modern Poets",
    "The World's Greatest Poems",
    "NOTE",
    "Contents of the Collection",
    "The Delphi Classics Catalogue",
}

# Work titles that are section headers within a poet, not poem titles
WORK_HEADERS = {
    "The Iliad Extracts",
    "The Odyssey Extracts",
    "The Aeneid",
    "The Georgics",
    "The Eclogues",
}

# Date overrides / estimates for poets without dates in text
POET_DATES = {
    "Homer": 1715,  # Pope's Iliad
    "Virgil": 1697,  # Dryden's Aeneid
    "Ovid": 1599,  # Marlowe's Amores
    "Geoffrey Chaucer": 1400,
    "Traditional Medieval Ballads": 1400,
}

INTERMEDIATE_DIR = Path("corpus/intermediate")

# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


def epub_to_html(epub_path: str) -> str:
    """Convert epub to HTML via Calibre, return HTML string."""
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


# ---------------------------------------------------------------------------
# HTML Parsing
# ---------------------------------------------------------------------------


def extract_text(el) -> str:
    """Get all text from an element, stripping tags."""
    return unescape(el.text_content()).strip()


def extract_poem_text(el) -> list[str]:
    """Extract lines of poetry from a paragraph element.

    Lines are separated by <br> tags. Strips line numbers (spans with
    class t15) and navigation links.
    """
    # Remove line-number spans (class t15)
    for span in el.findall(".//span[@class='t15']"):
        span.text = ""
        span.tail = span.tail or ""

    # Get inner HTML, split on <br>, strip tags
    raw = lxml_html.tostring(el, encoding="unicode")
    # Remove the wrapping <p> tag
    raw = re.sub(r"^<p[^>]*>", "", raw)
    raw = re.sub(r"</p>$", "", raw)

    # Split on <br> variants
    parts = re.split(r"<br\s*/?\s*>|<br\s+class=[^>]*>", raw)

    lines = []
    for part in parts:
        # Strip all remaining HTML tags
        text = re.sub(r"<[^>]+>", "", part)
        text = unescape(text)
        # Normalize whitespace but preserve leading spaces (indentation)
        text = text.rstrip()
        # Replace &nbsp; sequences with spaces
        text = text.replace("\xa0", " ")
        if text.strip():
            lines.append(text)

    return lines


def parse_date_from_attribution(text: str) -> int | None:
    """Extract birth-death dates from attribution like 'William Blake (1757-1827)'."""
    m = re.search(r"\((\d{4})\s*[–—-]\s*(\d{4})\)", text)
    if m:
        birth, death = int(m.group(1)), int(m.group(2))
        # Use approximate floruit
        return birth + (death - birth) // 2
    m = re.search(r"\((\d{4})\)", text)
    if m:
        return int(m.group(1))
    return None


def parse_translator(text: str) -> str | None:
    """Extract translator name from 'Translated by X' line."""
    m = re.match(r"Translated by (.+)", text.strip())
    return m.group(1).strip() if m else None


def slugify(text: str) -> str:
    """Convert text to lowercase slug for IDs."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s]+", "-", text)
    return text[:60]


def is_nav_link(el) -> bool:
    """Check if element is a navigation link (List of Poems/Poets)."""
    text = extract_text(el)
    return "List of Poems" in text or "List of Poets" in text


def is_poem_content(el) -> bool:
    """Check if a <p> element contains poem text (not nav, not image-only)."""
    if is_nav_link(el):
        return False
    # Image-only paragraphs
    if el.find(".//img") is not None and not extract_text(el):
        return False
    text = extract_text(el).strip()
    if not text or text == "\xa0" or text == " ":
        return False
    return True


def _build_poet_toc(doc) -> set[str]:
    """Extract the set of calibre_link anchor IDs that are poet headings.

    Reads the 'Contents of the Collection' section of the TOC, where each
    p19 link points to a poet's heading div.
    """
    poet_ids = set()
    collecting = False
    for el in doc.iter():
        if el.tag == "h1" and "Contents" in el.text_content():
            collecting = True
            continue
        if not collecting:
            continue
        # Stop at the alphabetical poem list
        if el.tag == "h1" and "Alphabetical" in el.text_content():
            break
        if el.tag == "p" and el.get("class") == "p19":
            a = el.find(".//a[@href]")
            if a is not None:
                href = a.get("href", "")
                if href.startswith("#"):
                    poet_ids.add(href[1:])
    return poet_ids


def _div_has_anchor(div, anchor_ids: set[str]) -> bool:
    """Check if a div contains any <a> with an id in anchor_ids."""
    for a in div.findall(".//a[@id]"):
        if a.get("id") in anchor_ids:
            return True
    return False


def parse_html(html_content: str) -> dict:
    """Parse the Delphi anthology HTML into structured data.

    Returns dict of {poet_slug: intermediate_json_dict}
    """
    doc = lxml_html.fromstring(html_content)

    # Build set of anchor IDs that mark poet-introduction divs
    poet_anchor_ids = _build_poet_toc(doc)
    print(f"Found {len(poet_anchor_ids)} poet TOC entries")

    # Each poem/section is in a <div class="calibre">
    divs = doc.findall(".//div[@class='calibre']")
    print(f"Found {len(divs)} div sections")

    poets = {}  # slug -> {author, date, poems: [...]}
    current_poet = None
    current_poet_date = None
    current_work = None  # sub-work like "The Iliad Extracts"
    current_translator = None
    skip_until_next_poet = False

    for div in divs:
        h2s = div.findall(".//h2")
        if not h2s:
            continue

        heading_text = extract_text(h2s[0])

        # Skip empty headings
        if not heading_text.strip():
            continue

        # Skip section headings
        if heading_text in SECTION_HEADINGS:
            continue

        # Skip Delphi catalogue section at end
        if "Delphi" in heading_text and "Catalogue" in heading_text:
            skip_until_next_poet = True
            continue

        paras = div.findall(".//p")

        # Attribution line check — is there a "(YYYY-YYYY)" pattern?
        attribution_texts = [
            extract_text(p) for p in paras
            if re.search(r"\(\d{4}\s*[–—-]\s*\d{4}\)", extract_text(p))
        ]

        # Translator check
        translator_texts = [
            extract_text(p) for p in paras
            if extract_text(p).startswith("Translated by")
        ]

        # Detect poet heading using TOC anchor IDs
        is_poet_intro = _div_has_anchor(div, poet_anchor_ids)

        if is_poet_intro:
            current_poet = heading_text
            current_poet_date = POET_DATES.get(current_poet)
            current_work = None
            current_translator = None
            skip_until_next_poet = False

            if current_poet in SKIP_POETS:
                skip_until_next_poet = True
                print(f"  Skipping poet: {current_poet}")
                continue

            print(f"  Poet: {current_poet}")

            # Some poet intro divs also contain attribution with dates
            for attr_text in attribution_texts:
                d = parse_date_from_attribution(attr_text)
                if d:
                    current_poet_date = d
            continue

        if skip_until_next_poet:
            continue

        if current_poet is None:
            continue

        # Work sub-headers (e.g. "The Iliad Extracts", "The Aeneid")
        if heading_text in WORK_HEADERS:
            current_work = heading_text
            # Check for translator on same div (sometimes on next line)
            for p in paras:
                t = parse_translator(extract_text(p))
                if t:
                    current_translator = t
            # Also check for second h2 in same div (poem title embedded)
            if len(h2s) > 1:
                # There's a poem title in the same div — process it below
                heading_text = extract_text(h2s[-1])
            else:
                continue

        # This should be a poem
        poem_title = heading_text

        # Get attribution and date
        poet_for_poem = current_poet
        date_for_poem = current_poet_date

        for attr_text in attribution_texts:
            d = parse_date_from_attribution(attr_text)
            if d:
                date_for_poem = d

        # Check for translator
        translator = current_translator
        for t_text in translator_texts:
            t = parse_translator(t_text)
            if t:
                translator = t

        # Handle translated poets
        if current_poet in TRANSLATED_POETS:
            config = TRANSLATED_POETS[current_poet]
            if translator and translator in config["keep_translators"]:
                # Keep it, attribute to translator
                poet_for_poem = translator
                work_title = f"{current_poet} ({current_work or 'Poems'})"
            elif translator and translator not in config["keep_translators"]:
                # Skip — wrong translator
                continue
            elif not translator:
                # No translator identified — skip to be safe
                continue
        else:
            work_title = current_work or "Delphi Poetry Anthology"

        # Extract verse lines
        verse_lines = []
        for p in paras:
            if not is_poem_content(p):
                continue
            p_text = extract_text(p)
            # Skip attribution lines
            if re.match(r"^(Translated by |.*\(\d{4})", p_text):
                continue
            # Skip year-only lines
            if re.match(r"^\d{4}$", p_text.strip()):
                continue
            # Skip Latin epigraphs and similar
            if p.get("class") == "p27" and len(p_text) < 100:
                continue

            lines = extract_poem_text(p)
            if lines:
                verse_lines.extend(lines)
                # Mark stanza break between paragraphs
                verse_lines.append("")

        # Clean up: remove trailing empty lines, collapse multiple blanks
        while verse_lines and not verse_lines[-1].strip():
            verse_lines.pop()

        cleaned = []
        prev_blank = False
        for line in verse_lines:
            if not line.strip():
                if not prev_blank:
                    cleaned.append("")
                prev_blank = True
            else:
                cleaned.append(line)
                prev_blank = False
        verse_lines = cleaned

        if not verse_lines:
            continue

        # Build stanzas (split on blank lines)
        stanzas = []
        current_stanza_lines = []
        for line in verse_lines:
            if not line.strip():
                if current_stanza_lines:
                    stanzas.append({
                        "stanza_num": "",
                        "lines": current_stanza_lines,
                        "gaps": [],
                    })
                    current_stanza_lines = []
            else:
                current_stanza_lines.append(line)
        if current_stanza_lines:
            stanzas.append({
                "stanza_num": "",
                "lines": current_stanza_lines,
                "gaps": [],
            })

        if not stanzas:
            continue

        # Add to poet's collection
        slug = slugify(poet_for_poem)
        if slug not in poets:
            poets[slug] = {
                "author": poet_for_poem,
                "title": work_title,
                "date": str(date_for_poem) if date_for_poem else "",
                "poems": [],
            }

        poets[slug]["poems"].append({
            "title": poem_title,
            "stanzas": stanzas,
        })

    return poets


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_intermediate(poets: dict, output_dir: Path):
    """Write per-poet intermediate JSON files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    total_poems = 0
    for slug, data in sorted(poets.items()):
        n_poems = len(data["poems"])
        total_poems += n_poems

        out = {
            "tcp_id": "",
            "gutenberg_id": "",
            "delphi_source": "Delphi Poetry Anthology",
            "author": data["author"],
            "title": data["title"],
            "date": data["date"],
            "source": "Delphi",
            "poems": data["poems"],
        }

        filename = f"DELPHI_{slug}.json"
        outpath = output_dir / filename
        with open(outpath, "w") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)

        print(f"  {filename}: {n_poems} poems")

    print(f"\nTotal: {len(poets)} poets, {total_poems} poems")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    default_path = os.path.expanduser("~/Desktop/Delphi Poetry Anthology.epub")
    epub_path = sys.argv[1] if len(sys.argv) > 1 else default_path

    if not os.path.exists(epub_path):
        print(f"File not found: {epub_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Converting {epub_path} to HTML...")
    html_content = epub_to_html(epub_path)
    print(f"HTML size: {len(html_content):,} bytes")

    print("\nParsing poems...")
    poets = parse_html(html_content)

    print(f"\nWriting intermediate JSON to {INTERMEDIATE_DIR}/...")
    write_intermediate(poets, INTERMEDIATE_DIR)

    print("\nDone. Run chunk_corpus.py and ingest_to_chroma.py to embed.")


if __name__ == "__main__":
    main()
