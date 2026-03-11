#!/usr/bin/env python3
"""
parse_queen_mab.py — Parse Queen Mab from marxists.org HTML into intermediate JSON.

Structure: 9 cantos. Verse paragraphs in <p class="indentb"> or <p class="quoteb">.
Canto breaks at <h3> (roman numerals). No line numbers.

Usage:
    python scripts/parse_queen_mab.py
"""

import json
import re
from html import unescape
from pathlib import Path

from lxml import html as lhtml

RAW_PATH = Path("corpus/raw/gutenberg/shelley_queen_mab.html")
OUT_PATH = Path("corpus/intermediate/MARXISTS_shelley-queen-mab.json")


def _clean_line(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).rstrip()
    text = text.replace("\xa0", " ")
    return text


def parse():
    with open(RAW_PATH, encoding="iso-8859-1") as f:
        doc = lhtml.fromstring(f.read())

    # Walk through all elements in document order.
    # Split into cantos at h3 boundaries. Verse in <p> elements.
    cantos = []
    current_canto = 1
    current_stanzas = []
    current_stanza = []
    in_poem = False
    seen_first_h3 = False

    for el in doc.iter():
        if el.tag == "h3":
            # Canto break
            seen_first_h3 = True
            if current_stanza:
                current_stanzas.append(current_stanza)
                current_stanza = []
            if current_stanzas:
                cantos.append((current_canto, current_stanzas))
                current_stanzas = []
            text = el.text_content().strip()
            roman = text.strip().rstrip(".")
            current_canto = _roman_to_int(roman) or len(cantos) + 1
            in_poem = True
            continue

        if not seen_first_h3:
            # Skip everything before first canto (title, dedication, etc.)
            # But detect the start of Canto I if it comes before any h3
            if el.tag == "p" and el.get("class", "") == "indentb":
                # Check if previous sibling is h1 (poem start without h3 for Canto I)
                prev = el.getprevious()
                if prev is not None and prev.tag == "h1":
                    seen_first_h3 = True
                    in_poem = True
                    current_canto = 1
                    # Fall through to process this <p>
                else:
                    continue
            else:
                continue

        if not in_poem:
            continue

        if el.tag == "p":
            cls = el.get("class", "")
            if cls not in ("indentb", "quoteb"):
                continue

            # Get inner HTML, split on <br>
            raw = lhtml.tostring(el, encoding="unicode")
            raw = re.sub(r"^<p[^>]*>", "", raw)
            raw = re.sub(r"</p>$", "", raw)
            parts = re.split(r"<br\s*/?\s*>", raw)

            lines = []
            for part in parts:
                line = _clean_line(part)
                if line.strip():
                    lines.append(line)

            if lines:
                # Each <p> is a verse paragraph — treat as a stanza
                if current_stanza:
                    current_stanzas.append(current_stanza)
                current_stanza = lines

    # Flush
    if current_stanza:
        current_stanzas.append(current_stanza)
    if current_stanzas:
        cantos.append((current_canto, current_stanzas))

    # Build poems (one per canto)
    poems = []
    total_lines = 0
    for canto_num, stanzas in cantos:
        n_lines = sum(len(s) for s in stanzas)
        total_lines += n_lines
        poems.append({
            "title": f"Queen Mab, Canto {canto_num}",
            "stanzas": [
                {"stanza_num": "", "lines": s}
                for s in stanzas
            ],
        })
        print(f"  Canto {canto_num}: {len(stanzas)} stanzas, {n_lines} lines")

    print(f"\n  {len(poems)} cantos, {total_lines} lines total")

    output = {
        "tcp_id": "",
        "gutenberg_id": "",
        "author": "Percy Bysshe Shelley",
        "title": "Queen Mab",
        "date": "1813",
        "source": "marxists.org",
        "poems": poems,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"  Written: {OUT_PATH}")


def _roman_to_int(s: str) -> int | None:
    roman_map = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    s = s.strip().upper()
    if not s or not all(c in roman_map for c in s):
        return None
    result = 0
    for i, c in enumerate(s):
        val = roman_map[c]
        if i + 1 < len(s) and roman_map[s[i + 1]] > val:
            result -= val
        else:
            result += val
    return result


if __name__ == "__main__":
    parse()
