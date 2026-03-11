#!/usr/bin/env python3
"""
parse_coleridge.py — Parse Gutenberg #29090 Complete Poetical Works of Coleridge.

Scholarly edition (E.H. Coleridge, 1912) with extensive apparatus.
HTML structure: h2/h3 titles, div.poem > div.stanza > span.i0/i1/i2...
Footnotes in div.footnote, line numbers in span.linenum, page numbers in span.pn.

We skip:
  - Editor's preface and introduction (before first poem)
  - Footnotes and textual notes
  - Latin/Greek poems
  - Dramatic works (Remorse, Zapolya, Osorio)
  - The appendices and bibliographical matter

Usage:
    python scripts/parse_coleridge.py
"""

import json
import re
from html import unescape
from pathlib import Path

from lxml import html as lhtml

RAW_PATH = Path("corpus/raw/gutenberg/coleridge_complete.html")
OUT_PATH = Path("corpus/intermediate/GUT_coleridge-complete.json")

# Poems/sections to SKIP (by title substring, case-insensitive)
SKIP_TITLES = {
    # Plays and play structure
    "THE FALL OF ROBESPIERRE",
    "OSORIO",
    "REMORSE",
    "ZAPOLYA",
    "ACT I", "ACT II", "ACT III", "ACT IV", "ACT V",
    "ACT THE FIRST", "ACT THE SECOND", "ACT THE THIRD",
    "ACT THE FOURTH", "ACT THE FIFTH",
    "PROLOGUE", "EPILOGUE",
    "THE PRELUDE, ENTITLED",
    # Latin/Greek
    "LATIN VERSION",
    "GREEK ODE",
    "AD LYRAM",
    "LATIN ALCAICS",
    "LATIN HEXAMETERS",
    "LATIN LINES",
    # Editorial
    "FOOTNOTES",
    "APPENDIX",
    "BIBLIOGRAPH",
    "TEXTUAL",
    "PREFACE TO",
    "PREFACE",
    "CONTENTS",
    "TABLE OF",
    "INDEX OF FIRST LINES",
}


def _clean_line(text: str) -> str:
    """Clean a verse line extracted from span elements."""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).strip()
    # Strip line numbers
    text = re.sub(r"\s*\d+\s*$", "", text)
    # Strip page number markers like [8] and footnote markers like [297:3]
    text = re.sub(r"\[\d+(?::\d+)?\]", "", text)
    return text.strip()


def _clean_title(el) -> str:
    """Extract clean title from an h2 or h3 element."""
    text = el.text_content()
    # Remove footnote markers like [8:1] and page markers like [8]
    text = re.sub(r"\[\d+(?::\d+)?\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Remove trailing period
    text = re.sub(r"\.\s*$", "", text)
    return text


def _should_skip(title: str) -> bool:
    """Check if a poem should be skipped based on title."""
    upper = title.upper()
    for pattern in SKIP_TITLES:
        if pattern in upper:
            return True
    return False


def _extract_stanzas(poem_div) -> list[list[str]]:
    """Extract stanzas from a div.poem element."""
    stanzas = []
    for stanza_div in poem_div.findall(".//div[@class='stanza']"):
        lines = []
        for span in stanza_div.findall("span"):
            cls = span.get("class", "")
            # Skip page numbers and line numbers
            if "pn" in cls or "linenum" in cls:
                continue
            if cls.startswith("i") or "indent" in cls:
                text = _clean_line(lhtml.tostring(span, encoding="unicode"))
                if text:
                    lines.append(text)
        if lines:
            stanzas.append(lines)
    return stanzas


def parse():
    with open(RAW_PATH, encoding="utf-8") as f:
        raw = f.read()

    tree = lhtml.fromstring(raw)

    # Find all h2 elements (poem titles) and their associated div.poem
    poems = []
    total_lines = 0
    skipped = 0

    # Strategy: iterate all h2 elements, find the next div.poem after each
    h2s = tree.xpath("//h2")
    print(f"  Found {len(h2s)} h2 headings")

    for h2 in h2s:
        title = _clean_title(h2)

        if not title or len(title) < 2:
            continue

        if _should_skip(title):
            skipped += 1
            continue

        # Find the next div.poem sibling or nearby element
        # Walk forward from this h2 through siblings
        poem_divs = []
        el = h2.getnext()
        while el is not None:
            if el.tag == "h2":
                break  # Next poem title
            if el.tag == "div" and el.get("class", "") == "poem":
                poem_divs.append(el)
            elif el.tag == "div":
                # Check for nested poem divs
                for sub in el.findall(".//div[@class='poem']"):
                    poem_divs.append(sub)
            el = el.getnext()

        if not poem_divs:
            continue

        all_stanzas = []
        for pd in poem_divs:
            stanzas = _extract_stanzas(pd)
            all_stanzas.extend(stanzas)

        if not all_stanzas:
            continue

        n_lines = sum(len(s) for s in all_stanzas)

        # Skip very short fragments (< 3 lines) unless they're titled
        if n_lines < 3 and len(title) < 10:
            continue

        total_lines += n_lines
        poems.append({
            "title": title,
            "stanzas": [
                {"stanza_num": "", "lines": st}
                for st in all_stanzas
            ],
        })

    print(f"  Skipped {skipped} editorial/play/Latin sections")
    print(f"  Extracted {len(poems)} poems, {total_lines} lines")

    output = {
        "tcp_id": "",
        "gutenberg_id": "29090",
        "author": "Samuel Taylor Coleridge",
        "title": "Complete Poetical Works (E.H. Coleridge, 1912)",
        "date": "1834",
        "source": "Gutenberg",
        "poems": poems,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"  Written: {OUT_PATH}")


if __name__ == "__main__":
    parse()
