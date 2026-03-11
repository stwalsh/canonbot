#!/usr/bin/env python3
"""
parse_don_juan.py — Parse Gutenberg Don Juan (ID 21700) into intermediate JSON.

Structure: 18 div.chapter elements (Dedication + Cantos I–XVII).
Each chapter has one <p class="noindent"> containing the entire canto,
with stanzas (ottava rima, 8 lines) separated by <br><br>.

Usage:
    python scripts/parse_don_juan.py
"""

import json
import re
from html import unescape
from pathlib import Path

from lxml import html as lhtml

RAW_PATH = Path("corpus/raw/gutenberg/byron_don_juan.htm")
OUT_PATH = Path("corpus/intermediate/GUT_byron-don-juan.json")


def _clean_line(text: str) -> str:
    """Normalize a verse line."""
    text = text.replace("\xa0", " ")
    text = text.strip()
    # Strip trailing line numbers
    text = re.sub(r"\s+\d+\s*$", "", text)
    return text


def _extract_stanzas_from_p(p_el) -> list[list[str]]:
    """Extract stanzas from a single <p> element where stanzas are
    separated by double <br> (i.e. <br>\n<br>).
    """
    inner = lhtml.tostring(p_el, encoding="unicode")
    # Remove wrapping <p>
    inner = re.sub(r"^<p[^>]*>", "", inner)
    inner = re.sub(r"</p>\s*$", "", inner)

    # Split on double-br (stanza boundary)
    stanza_chunks = re.split(r"<br\s*/?\s*>\s*\n?\s*<br\s*/?\s*>", inner)

    stanzas = []
    for chunk in stanza_chunks:
        # Split on single <br> within stanza
        parts = re.split(r"<br\s*/?\s*>", chunk)
        lines = []
        for part in parts:
            text = re.sub(r"<[^>]+>", "", part)
            text = unescape(text)
            text = _clean_line(text)
            if text.strip():
                lines.append(text)
        if lines:
            stanzas.append(lines)
    return stanzas


def parse():
    with open(RAW_PATH) as f:
        raw = f.read()

    tree = lhtml.fromstring(raw)
    chapters = tree.xpath('//div[@class="chapter"]')
    print(f"  Found {len(chapters)} chapters")

    poems = []
    total_stanzas = 0
    total_lines = 0

    for ch in chapters:
        h2 = ch.xpath(".//h2")
        if not h2:
            continue
        heading = h2[0].text_content().strip()
        # Clean heading
        heading = re.sub(r"\s+", " ", heading)

        # Extract verse from p.noindent
        ps = ch.xpath('.//p[@class="noindent"]')
        if not ps:
            print(f"    SKIP {heading}: no p.noindent")
            continue

        all_stanzas = []
        for p in ps:
            stanzas = _extract_stanzas_from_p(p)
            all_stanzas.extend(stanzas)

        if not all_stanzas:
            print(f"    SKIP {heading}: no stanzas extracted")
            continue

        n_lines = sum(len(s) for s in all_stanzas)
        print(f"    {heading}: {len(all_stanzas)} stanzas, {n_lines} lines")
        total_stanzas += len(all_stanzas)
        total_lines += n_lines

        # Format title
        title = heading
        if title == "DEDICATION":
            title = "Don Juan: Dedication"
        elif "CANTO" in title.upper():
            # "CANTO THE FIRST" → "Don Juan: Canto I"
            title = f"Don Juan: {_normalize_canto_title(title)}"

        poems.append({
            "title": title,
            "stanzas": [
                {"stanza_num": str(i + 1), "lines": st}
                for i, st in enumerate(all_stanzas)
            ],
        })

    print(f"\n  Total: {len(poems)} sections, {total_stanzas} stanzas, {total_lines} lines")

    output = {
        "tcp_id": "",
        "gutenberg_id": "21700",
        "author": "George Gordon Byron Lord Byron",
        "title": "Don Juan",
        "date": "1824",
        "source": "Gutenberg",
        "poems": poems,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"  Written: {OUT_PATH}")


_ORDINALS = {
    "FIRST": "I", "SECOND": "II", "THIRD": "III", "FOURTH": "IV",
    "FIFTH": "V", "SIXTH": "VI", "SEVENTH": "VII", "EIGHTH": "VIII",
    "NINTH": "IX", "TENTH": "X", "ELEVENTH": "XI", "TWELFTH": "XII",
    "THIRTEENTH": "XIII", "FOURTEENTH": "XIV", "FIFTEENTH": "XV",
    "SIXTEENTH": "XVI", "SEVENTEENTH": "XVII",
}


def _normalize_canto_title(heading: str) -> str:
    """'CANTO THE FIRST' → 'Canto I'"""
    upper = heading.upper().strip()
    for ordinal, roman in _ORDINALS.items():
        if ordinal in upper:
            return f"Canto {roman}"
    # Fallback
    return heading.title()


if __name__ == "__main__":
    parse()
