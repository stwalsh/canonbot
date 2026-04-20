#!/usr/bin/env python3
"""
parse_swift.py — Parse Delphi Complete Works of Jonathan Swift epub
into intermediate JSON (poetry + prose).

Extracts:
  - Poetry: individual poems from "The Poems of Jonathan Swift" section
  - Prose: chapter-level chunks from selected satires

Usage:
    python scripts/parse_swift.py "/path/to/Complete Works of Jonathan Swift.epub"
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

AUTHOR = "Jonathan Swift"
SLUG = "swift"

# Prose sections to extract: (h1 title substring, start file, end file sentinel)
# We'll detect these from the TOC scan.
PROSE_SECTIONS = {
    "A Tale of a Tub",
    "The Battle of the Books",
    "Gulliver\u2019s Travels, 1735",  # use 1735 edition
    "A Modest Proposal",
    "The Bickerstaff-Partridge Papers",
    "Directions to Servants",
    "A Complete Collection of Genteel and Ingenious Conversation",
    "An Examination of Certain Abuses",
    "Drapier\u2019s Letters",
}

# Skip these h1 sections entirely
SKIP_SECTIONS = {
    "The Delphi Classics Catalogue",
    "The Biographies",
    "Gulliver\u2019s Travels, 1726",  # use 1735 instead
    "A Journal to Stella",
    "The Sermons",
    "Three Sermons",
    "Brotherly Love and Other Sermons",
    "Other Religious Works",
    "Swift\u2019s Religious Works",
    "Swift\u2019s Political Works",
    "The Historical Works",
    "The History of the Four Last Years of the Queen",
    "An Abstract of the History of England",
    "The Journalism",
}

# Structural h2s to skip (not poem titles, not chapter titles)
STRUCTURAL_H2S = {
    "COPYRIGHT", "NOTE", "CONTENTS", "PREFACE", "INTRODUCTION",
    "INTRODUCTION.", "Series Contents",
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
    return unescape(el.text_content()).strip()


def extract_poem_lines(el) -> list[str]:
    """Extract lines of poetry from a <p> element. Lines separated by <br>."""
    for span in el.findall(".//span"):
        cls = span.get("class", "")
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
        text = unescape(text).rstrip().replace("\xa0", " ")
        if text.strip():
            lines.append(text)
    return lines


def extract_prose_paragraphs(el) -> str:
    """Extract prose text from a <p> element."""
    text = extract_text(el)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_title(title: str) -> str:
    """Clean up a heading title."""
    title = unescape(title)
    title = re.sub(r"\.\s*$", "", title)
    title = title.replace("\xa0", " ")
    return title.strip()


def parse_swift(html_content: str) -> tuple[list[dict], list[dict]]:
    """Parse Swift HTML into poetry + prose intermediate JSON.

    Returns (poems, prose_pieces).
    """
    doc = lhtml.fromstring(html_content)
    divs = doc.findall(".//div[@class='calibre']")
    print(f"  Found {len(divs)} div sections")

    poems = []
    prose_pieces = []

    mode = None  # "poetry", "prose", "skip"
    current_section = None  # h1 section name for prose
    current_chapter = None  # h2 chapter name within prose
    current_prose_paras = []  # accumulating prose paragraphs

    def _flush_prose():
        nonlocal current_prose_paras, current_chapter
        if current_prose_paras and current_section:
            text = "\n\n".join(current_prose_paras)
            if len(text.split()) >= 30:  # skip tiny fragments
                chapter_title = current_chapter or current_section
                prose_pieces.append({
                    "title": chapter_title,
                    "work": current_section,
                    "author": AUTHOR,
                    "date": _date_for_work(current_section),
                    "type": "prose",
                    "genre": _genre_for_work(current_section),
                    "text": text,
                })
            current_prose_paras = []

    for div in divs:
        h1s = div.findall(".//h1")
        h2s = div.findall(".//h2")

        # Handle h1 section boundaries
        if h1s:
            h1_text = clean_title(extract_text(h1s[0]))
            h1_class = h1s[0].get("class", "")

            # Check if this heading matches a prose target (any class)
            is_prose_target = any(s in h1_text for s in PROSE_SECTIONS)
            is_skip_target = any(s in h1_text for s in SKIP_SECTIONS)
            is_poetry = "Poem" in h1_text or "Poetry" in h1_text

            # Major sections (h1, h2 class) or known targets at any class level
            if h1_class in ("h1", "h2") or is_prose_target or is_skip_target or is_poetry:
                if is_poetry:
                    _flush_prose()
                    mode = "poetry"
                    current_section = h1_text
                    print(f"  POETRY section: {h1_text}")
                elif is_skip_target:
                    _flush_prose()
                    mode = "skip"
                    print(f"  SKIP section: {h1_text}")
                elif is_prose_target:
                    _flush_prose()
                    mode = "prose"
                    current_section = h1_text
                    current_chapter = None
                    print(f"  PROSE section: {h1_text}")
                elif h1_class in ("h1", "h2"):
                    # Unknown top-level section
                    _flush_prose()
                    mode = "skip"
                    print(f"  SKIP section (default): {h1_text}")
                else:
                    # Sub-section h1 (h3/h4/h5) within current mode
                    if mode == "prose":
                        _flush_prose()
                        current_chapter = h1_text
                continue

            # Sub-section h1s (h3, h4, h5 class) not matching any target
            if h1_class in ("h3", "h4", "h5"):
                if mode == "prose":
                    _flush_prose()
                    current_chapter = h1_text
                continue

        if mode == "skip" or mode is None:
            continue

        # Handle h2s — poem titles (poetry mode) or chapter titles (prose mode)
        if h2s:
            h2_text = clean_title(extract_text(h2s[0]))
            h2_class = h2s[0].get("class", "")

            if h2_text.upper() in STRUCTURAL_H2S:
                continue

            if mode == "poetry":
                # This is a poem
                paras = div.findall(".//p")
                stanzas = []
                current_stanza = []

                for p in paras:
                    p_text = extract_text(p)
                    if not p_text.strip():
                        continue
                    if re.match(r"^\d{4}$", p_text.strip()):
                        continue
                    if "List of Poems" in p_text:
                        continue

                    lines = extract_poem_lines(p)
                    if lines:
                        if current_stanza:
                            stanzas.append(current_stanza)
                        current_stanza = lines

                if current_stanza:
                    stanzas.append(current_stanza)

                if stanzas:
                    total_lines = sum(len(s) for s in stanzas)
                    poems.append({
                        "title": h2_text,
                        "stanzas": [
                            {"stanza_num": "", "lines": s}
                            for s in stanzas
                        ],
                        "total_lines": total_lines,
                        "author": AUTHOR,
                        "date": _date_from_title(h2_text),
                        "work": "Poems",
                        "type": "verse",
                    })
                continue

            if mode == "prose":
                _flush_prose()
                current_chapter = h2_text
                # Fall through to collect paragraphs

        # Collect prose paragraphs
        if mode == "prose":
            paras = div.findall(".//p")
            for p in paras:
                text = extract_prose_paragraphs(p)
                if text and len(text) > 20:
                    # Skip navigation / TOC-like content
                    if "List of" in text[:20] or "CONTENTS" in text[:20]:
                        continue
                    current_prose_paras.append(text)

    # Final flush
    _flush_prose()

    return poems, prose_pieces


def _date_from_title(title: str) -> str:
    """Extract a year from a poem title if present."""
    m = re.search(r"\b(1[67]\d{2})\b", title)
    return m.group(1) if m else "1720"  # default floruit


def _date_for_work(work: str) -> str:
    """Return approximate date for a prose work."""
    dates = {
        "A Tale of a Tub": "1704",
        "The Battle of the Books": "1704",
        "Gulliver\u2019s Travels, 1735": "1735",
        "A Modest Proposal": "1729",
        "The Bickerstaff-Partridge Papers": "1708",
        "Directions to Servants": "1745",
        "A Complete Collection of Genteel and Ingenious Conversation": "1738",
        "An Examination of Certain Abuses": "1732",
        "Drapier\u2019s Letters": "1724",
    }
    for key, date in dates.items():
        if key in work:
            return date
    return "1720"


def _genre_for_work(work: str) -> str:
    """Return genre tag for a prose work."""
    if "Gulliver" in work:
        return "satire"
    if "Tale of a Tub" in work or "Battle of the Books" in work:
        return "satire"
    if "Modest Proposal" in work:
        return "satire"
    if "Bickerstaff" in work:
        return "satire"
    if "Drapier" in work:
        return "polemic"
    if "Directions" in work or "Genteel" in work:
        return "satire"
    return "prose"


def write_intermediate(poems: list[dict], prose_pieces: list[dict]):
    """Write intermediate JSON files."""
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)

    # Poetry
    if poems:
        out_path = INTERMEDIATE_DIR / f"DELPHI_{SLUG}_poems.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(poems, f, indent=2, ensure_ascii=False)
        total_lines = sum(p["total_lines"] for p in poems)
        print(f"  Wrote {len(poems)} poems ({total_lines} lines) → {out_path}")

    # Prose — one file per work
    works = {}
    for piece in prose_pieces:
        work = piece["work"]
        works.setdefault(work, []).append(piece)

    for work_name, pieces in works.items():
        safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", work_name).strip("_").lower()
        out_path = INTERMEDIATE_DIR / f"DELPHI_{SLUG}_{safe_name}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(pieces, f, indent=2, ensure_ascii=False)
        total_words = sum(len(p["text"].split()) for p in pieces)
        print(f"  Wrote {len(pieces)} chapters ({total_words:,} words) from '{work_name}' → {out_path}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/parse_swift.py path/to/swift.epub")
        sys.exit(1)

    epub_path = sys.argv[1]
    print(f"Parsing Swift: {epub_path}")
    html_content = epub_to_html(epub_path)
    print(f"  Converted to HTML ({len(html_content):,} chars)")

    poems, prose_pieces = parse_swift(html_content)

    print(f"\n  Summary:")
    print(f"    Poems: {len(poems)}")
    print(f"    Prose pieces: {len(prose_pieces)}")

    write_intermediate(poems, prose_pieces)


if __name__ == "__main__":
    main()
