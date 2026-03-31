#!/usr/bin/env python3
"""
parse_prose.py — Parse prose works from Delphi epub into intermediate JSON.

Extracts essays/chapters as prose paragraphs (not verse stanzas).
Uses a selection filter to ingest only curated essays.

Usage:
    python scripts/parse_prose.py "William Hazlitt" path/to/epub
    python scripts/parse_prose.py "William Hazlitt" path/to/epub --list  # just list available essays
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

# Sections to skip (plays, biographies, catalogues, etc.)
SKIP_SECTIONS = {
    "The Plays", "The Play", "The Biographies", "The Biography",
    "The Autobiographies", "The Delphi Classics Catalogue",
    "List of Essays in Chronological Order",
    "List of Essays in Alphabetical Order",
    "List of Poems in Chronological Order",
    "List of Poems in Alphabetical Order",
    "The Criticism",  # secondary criticism about the author
    "Essays Index",
}

STRUCTURAL_H2S = {
    "COPYRIGHT", "NOTE", "CONTENTS", "PREFACE", "INTRODUCTION",
    "BIBLIOGRAPHICAL NOTE", "ADVERTISEMENT",
}

# Author-specific configuration
PROSE_CONFIG = {
    "William Hazlitt": {
        "author": "William Hazlitt",
        "date": "1821",
        "slug": "hazlitt",
        "type": "prose",
        "genre": "essay",
        # Paulin's Penguin selection — essay titles to include (substring match)
        "select_essays": [
            "Pleasure of Painting",
            "Same Subject",  # continuation of Pleasure of Painting
            "Landscape of Nicolas Poussin",
            "Kean",
            "Shylock",
            "Macready",
            "Othello",
            "Siddons",
            "Coriolanus",
            "Elizabethan",
            "On Gusto",
            "Shakespeare and Milton",
            "Indian Jugglers",
            "Character of Cobbett",
            "The Fight",
            "Jack Tar",
            "Hogarth",
            "Marriage",
            "Character of Burke",
            "Character of Mr. Burke",
            "Arts are Not Progressive",
            "On Envy",
            "Elgin Marbles",
            "Prose-Style",
            "Prose Style of Poets",
            "Gifford",
            "First Acquaintance with Poets",
            "Jeremy Bentham",
            "William Godwin",
            "Mr. Coleridge",
            "Mr Coleridge",
            "Mr. Wordsworth",
            "Mr Wordsworth",
            "Wordsworth",
            "Parliamentary Eloquence",
            "Spirit of Monarchy",
            "Toad",
            "What is the People",
            "Reason and Imagination",
            "Genius is Conscious",
            "Pleasure of Hating",
            "Hot and Cold",
            "Difference between Writing and Speaking",
            "Madame Pasta",
            "Vandyke",
            "Portrait of an English Lady",
            "On Wit",
            "Genius and Common Sense",
            "Farewell to Essay",
            "Letter-Bell",
            "Letter Bell",
            "On the Love of Life",
            "On the Love of the Country",
            "On Milton's Lycidas",
            "On Posthumous Fame",
        ],
        "skip_collections": set(),
    },
    "Samuel Johnson": {
        "author": "Samuel Johnson",
        "date": "1765",
        "slug": "johnson",
        "type": "prose",
        "genre": "criticism",
        # Lives of the Poets — selected lives covering corpus poets + key minor lives
        # Plus Shakespeare criticism, Dictionary preface, Jenyns review, Rasselas,
        # and selected Rambler/Idler essays
        "select_essays": [
            # Lives of the Poets
            "COWLEY",
            "DENHAM",
            "MILTON",
            "BUTLER",
            "ROCHESTER",
            "OTWAY",
            "WALLER",
            "DRYDEN",
            "PRIOR",
            "ADDISON",
            "SAVAGE",
            "SWIFT",
            "POPE",
            "THOMSON",
            "COLLINS",
            "GRAY",
            "ROSCOMMON",
            "PARNELL",
            "GAY",
            "YOUNG",
            "AKENSIDE",
            "SHENSTONE",
            "LYTTELTON",
            "PREFATORY NOTICE TO THE LIVES",
            # Shakespeare criticism
            "PREFACE TO THE PLAYS OF WILLIAM SHAKESPEARE",
            "Miscellaneous Observations on the Tragedy of Macbeth",
            "Proposals for Printing the Dramatick Works",
            # Dictionary
            "PREFACE TO A DICTIONARY",
            "Plan of a Dictionary",
            # Reviews
            "REVIEW OF A FREE ENQUIRY",  # Jenyns
            "REVIEW OF AN ESSAY ON THE WRITINGS AND GENIUS OF POPE",
            # Rasselas
            "PRINCE OF ABISSINIA",
            # Selected Rambler essays (by number — on criticism, authorship, self-delusion)
            "No. 1.",   # Opening — difficulty of first address
            "No. 2.",   # The necessity of occupation
            "No. 4.",   # On fiction / the novel
            "No. 36.",  # The terrours of death
            "No. 60.",  # On biography
            "No. 93.",  # The prejudice of faction
            "No. 106.", # The vanity of authorship
            "No. 125.", # The difficulty of defining comedy
            "No. 137.", # The vanity of literary reputation
            "No. 154.", # The art of living at the cost of others
            "No. 156.", # Envious criticism
            "No. 176.", # The power of novelty
            "No. 208.", # The final Rambler
            # Selected Idler essays
            "No. 58.",  # Idleness
            "No. 60.",  # Minim the Critic
            "No. 61.",  # Minim continued
            "No. 84.",  # Biography
            "No. 103.", # Horror of the last
        ],
        "skip_collections": {
            "A Voyage to Abyssinia",
            "Marmor Norfolciense",
            "A Compleat Vindication",
            "Debates in Parliament",
            "Prayers and Meditations",
            "A Conversation Between",
        },
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


def extract_paragraphs(div) -> list[str]:
    """Extract prose paragraphs from a div. Returns list of paragraph strings."""
    paras = div.findall(".//p")
    result = []
    for p in paras:
        text = extract_text(p)
        p_class = p.get("class", "")
        # Skip navigation, dates, images, very short items
        if not text or len(text) < 10:
            continue
        if "List of" in text:
            continue
        if p.find(".//img") is not None and not text:
            continue
        # Skip date-only paragraphs
        if re.match(r"^\[?\d{4}\]?\.?$", text.strip()):
            continue
        # Clean up whitespace
        text = re.sub(r"\s+", " ", text).strip()
        result.append(text)
    return result


def is_skip_section(text: str) -> bool:
    for s in SKIP_SECTIONS:
        if s.lower() in text.lower():
            return True
    return False


def matches_selection(title: str, select_list: list[str]) -> bool:
    """Check if an essay title matches any item in the selection list."""
    title_lower = title.lower()
    for sel in select_list:
        if sel.lower() in title_lower:
            return True
    return False


def parse_prose_html(html_content: str, config: dict, list_only: bool = False) -> list[dict]:
    """Parse Delphi prose epub into a list of essays with paragraphs."""
    doc = lhtml.fromstring(html_content)

    select_essays = config.get("select_essays")
    skip_collections = config.get("skip_collections", set())

    divs = doc.findall(".//div[@class='calibre']")
    print(f"  Found {len(divs)} div sections")

    essays = []
    in_prose_section = False
    in_skip_section = False
    current_collection = None
    section_as_essay = None  # when a section itself is the essay (e.g. Preface to Shakespeare)
    section_paragraphs = []

    for div in divs:
        h1s = div.findall(".//h1")
        h2s = div.findall(".//h2")

        # If we're collecting a section-as-essay and this div has no headers, collect its paragraphs
        if section_as_essay and not h1s and not h2s:
            paras = extract_paragraphs(div)
            if paras:
                section_paragraphs.extend(paras)
            continue

        # Check h1 section markers
        if h1s:
            h1_text = extract_text(h1s[0])
            h1_class = h1s[0].get("class", "")

            if h1_class in ("h1", "h2"):
                # Close any open section-as-essay
                if section_as_essay and section_paragraphs:
                    if not select_essays or matches_selection(section_as_essay, select_essays):
                        print(f"    + {section_as_essay:60s} {len(section_paragraphs)} paragraphs")
                        essays.append({
                            "title": section_as_essay,
                            "collection": "",
                            "paragraphs": section_paragraphs,
                        })
                section_as_essay = None
                section_paragraphs = []

                # Check if this section is explicitly selected before checking skip
                explicitly_selected = select_essays and matches_selection(h1_text, select_essays)
                if is_skip_section(h1_text) and not explicitly_selected:
                    in_prose_section = False
                    in_skip_section = True
                    print(f"  Skipping section: {h1_text}")
                else:
                    in_prose_section = True
                    in_skip_section = False
                    print(f"  Entering section: {h1_text}")
                    # Check if this section itself is an essay (no sub-h2s expected)
                    if explicitly_selected:
                        section_as_essay = re.sub(r"\s*\[.*$", "", h1_text).strip().rstrip(".")
                continue

            # Collection titles within section
            if h1_class in ("h3", "h4", "h5") and in_prose_section:
                current_collection = h1_text
                if current_collection in skip_collections:
                    print(f"  Skipping collection: {current_collection}")
                else:
                    print(f"    Collection: {current_collection}")
                continue

            # h1.h2 inside prose section = collection title
            if h1_class == "h2" and in_prose_section:
                current_collection = h1_text
                print(f"    Collection: {current_collection}")
                continue

        if not in_prose_section or in_skip_section:
            continue
        if current_collection in skip_collections:
            continue

        if not h2s:
            continue

        h2_text = extract_text(h2s[0])

        # Skip structural h2s
        if h2_text.strip().upper() in STRUCTURAL_H2S:
            continue
        if h2_text.strip().upper().startswith("CONTENTS"):
            continue
        if h2_text.strip().upper().startswith("BIBLIOGRAPHICAL"):
            continue

        # Clean title — remove date annotations like [JAN. 15, 1815.
        essay_title = re.sub(r"\s*\[.*$", "", h2_text).strip().rstrip(".")

        if list_only:
            print(f"      {essay_title}")
            continue

        # Check selection filter
        if select_essays and not matches_selection(essay_title, select_essays):
            continue

        # Extract paragraphs
        paragraphs = extract_paragraphs(div)
        if not paragraphs:
            continue

        print(f"    + {essay_title:60s} {len(paragraphs)} paragraphs")

        essays.append({
            "title": essay_title,
            "collection": current_collection or "",
            "paragraphs": paragraphs,
        })

    # Flush final section-as-essay
    if section_as_essay and section_paragraphs:
        if not select_essays or matches_selection(section_as_essay, select_essays):
            print(f"    + {section_as_essay:60s} {len(section_paragraphs)} paragraphs")
            essays.append({
                "title": section_as_essay,
                "collection": "",
                "paragraphs": section_paragraphs,
            })

    return essays


def save_intermediate(essays: list[dict], config: dict, epub_path: str) -> Path:
    """Save parsed essays as intermediate JSON."""
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    slug = config["slug"]
    output_path = INTERMEDIATE_DIR / f"PROSE_{slug}.json"

    data = {
        "source_file": os.path.basename(epub_path),
        "author": config["author"],
        "date": config.get("date", ""),
        "type": config.get("type", "prose"),
        "genre": config.get("genre", "essay"),
        "essays": essays,
    }

    output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    total_paras = sum(len(e["paragraphs"]) for e in essays)
    print(f"\n  {len(essays)} essays, {total_paras} paragraphs")
    print(f"  Written: {output_path}")

    # Show summary
    for e in essays[:10]:
        print(f"    {e['title']:60s} {len(e['paragraphs']):4d} paragraphs")
    if len(essays) > 10:
        print(f"    ... and {len(essays) - 10} more")

    return output_path


def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/parse_prose.py 'Author Name' path/to/epub [--list]")
        sys.exit(1)

    author_name = sys.argv[1]
    epub_path = sys.argv[2]
    list_only = "--list" in sys.argv

    if author_name not in PROSE_CONFIG:
        print(f"Unknown author: {author_name}")
        print(f"Available: {', '.join(PROSE_CONFIG.keys())}")
        sys.exit(1)

    config = PROSE_CONFIG[author_name]

    print(f"  Converting {epub_path}...")
    html_content = epub_to_html(epub_path)
    print(f"  HTML: {len(html_content):,} bytes")

    essays = parse_prose_html(html_content, config, list_only=list_only)

    if list_only:
        print(f"\n  Total essays found: {len(essays) if essays else 'see above'}")
    else:
        save_intermediate(essays, config, epub_path)


if __name__ == "__main__":
    main()
