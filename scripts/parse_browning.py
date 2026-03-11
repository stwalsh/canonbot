#!/usr/bin/env python3
"""
parse_browning.py — Parse Gutenberg #50954 Complete Browning into intermediate JSON.

Selective: skips early work (Pauline, Sordello), plays, Greek adaptations.
Keeps the dramatic monologues, Men and Women, Dramatis Personae, and
selected later works.

HTML structure:
  - h2 for major collections/works
  - h3 (or h5 within h3 groups) for individual poems
  - Two verse markup patterns:
    Pattern C: div.poem > div.stanza > div.i0/i1/i2...
    Pattern V: div.poetry(-container)? > div.stanza > div.verse

Usage:
    python scripts/parse_browning.py
"""

import json
import re
from html import unescape
from pathlib import Path

from lxml import html as lhtml

RAW_PATH = Path("corpus/raw/gutenberg/browning_complete.html")
OUT_PATH = Path("corpus/intermediate/GUT_browning-complete.json")

# h2 ids/text fragments to INCLUDE (everything else skipped)
INCLUDE_SECTIONS = {
    "PIPPA PASSES",
    "DRAMATIC LYRICS",
    "DRAMATIC_LYRICS",
    "DRAMATIC ROMANCES",
    "DRAMATIC_ROMANCES",
    "CHRISTMAS-EVE AND EASTER-DAY",
    "CHRISTMAS_EVE_AND_EASTER",
    "MEN AND WOMEN",
    "MEN_AND_WOMEN",
    "DRAMATIS PERSONÆ",
    "DRAMATIS PERSONAE",
    "DRAMATIS_PERSONAE",
    "PRINCE HOHENSTIEL-SCHWANGAU",
    "PRINCE_HOHENSTIEL-SCHWANGAU",
    "THE INN ALBUM",
    "THE_INN_ALBUM",
    "PACCHIAROTTO",
    "LA SAISIAZ",
    "LA_SAISIAZ",
    "DRAMATIC IDYLS",
    "DRAMATIC_IDYLS_FIRST",
    "SECOND SERIES",
    "SECOND_SERIES",
    "DRAMATIC_IDYLS_SECOND",
    "JOCOSERIA",
    "FERISHTAH'S FANCIES",
    "FERISHTAHS_FANCIES",
    "PARLEYINGS WITH CERTAIN PEOPLE",
    "PARLEYINGS",
    "ASOLANDO",
    "THE RING AND THE BOOK",
    "THE_RING_AND_THE_BOOK",
}

# Ring and the Book: map h3 id prefixes to speaker names
RING_SPEAKERS = {
    "I_THE_RING":   "Browning (narrator)",
    "II_HALF":      "Half-Rome",
    "III_THE":      "The Other Half-Rome",
    "IV_THE":       "Tertium Quid",
    "V_THE":        "Guido",
    "VI_THE":       "Caponsacchi",
    "VII_THE":      "Pompilia",
    "VIII_THE":     "Archangelis (Guido's lawyer)",
    "IX_THE":       "Bottini (prosecution)",
    "X_THE":        "The Pope",
    "XI_THE":       "Guido",
    "XII_THE":      "Browning (narrator)",
}


def _clean_line(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).strip()
    # Strip trailing line numbers
    text = re.sub(r"\s+\d+\s*$", "", text)
    return text


def _should_include(h2_el) -> bool:
    """Check if an h2 section should be included."""
    text = h2_el.text_content().strip().upper()
    h2_id = h2_el.get("id", "")
    # Check anchors inside
    for a in h2_el.findall(".//a"):
        aid = a.get("id", "")
        if aid:
            h2_id = aid

    for pattern in INCLUDE_SECTIONS:
        if pattern in text or pattern in h2_id.upper():
            return True
    return False


def _extract_stanzas_pattern_c(container) -> list[list[str]]:
    """Extract from div.poem > div.stanza > div.i0/i1/i2..."""
    stanzas = []
    for stanza_div in container.findall(".//div[@class='stanza']"):
        lines = []
        for div in stanza_div.findall("div"):
            cls = div.get("class", "")
            if cls.startswith("i") or "verse" in cls or "indent" in cls:
                text = _clean_line(lhtml.tostring(div, encoding="unicode"))
                if text:
                    lines.append(text)
        if lines:
            stanzas.append(lines)
    return stanzas


def _extract_stanzas_poetry(container) -> list[list[str]]:
    """Extract from div.poetry > div.stanza > div.verse."""
    stanzas = []
    for stanza_div in container.findall(".//div[@class='stanza']"):
        lines = []
        for div in stanza_div.findall("div"):
            cls = div.get("class", "")
            text = _clean_line(lhtml.tostring(div, encoding="unicode"))
            if not text:
                continue
            # Skip Roman numeral section markers that are standalone
            if re.match(r"^[IVXLC]+\.?$", text):
                continue
            lines.append(text)
        if lines:
            stanzas.append(lines)
    return stanzas


def _extract_stanzas(container) -> list[list[str]]:
    """Try both extraction patterns."""
    # Check which pattern is present
    poems = container.findall(".//div[@class='poem']")
    poetry = container.findall(".//div[@class='poetry']")
    poetry_container = container.findall(".//div[@class='poetry-container']")

    if poems:
        return _extract_stanzas_pattern_c(container)
    elif poetry or poetry_container:
        return _extract_stanzas_poetry(container)
    else:
        # Try both
        stanzas = _extract_stanzas_pattern_c(container)
        if not stanzas:
            stanzas = _extract_stanzas_poetry(container)
        return stanzas


def _clean_title(text: str) -> str:
    """Clean an h3/h5 poem title."""
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).strip()
    text = re.sub(r"\s+", " ", text)
    # Remove trailing period
    text = re.sub(r"\.\s*$", "", text)
    return text


def parse():
    with open(RAW_PATH, encoding="utf-8") as f:
        raw = f.read()

    tree = lhtml.fromstring(raw)
    _build_doc_order(tree)

    # Get all h2 elements
    h2s = tree.xpath("//h2")
    print(f"  Found {len(h2s)} h2 sections")

    poems = []
    total_lines = 0
    sections_used = 0

    for h2_idx, h2 in enumerate(h2s):
        if not _should_include(h2):
            continue

        section_name = h2.text_content().strip()
        section_name = re.sub(r"\s+", " ", section_name)
        sections_used += 1
        is_ring = "RING AND THE BOOK" in section_name.upper()

        # Find the content between this h2 and the next h2
        # Collect all h3 elements that are siblings/descendants
        # until the next h2
        next_h2 = h2s[h2_idx + 1] if h2_idx + 1 < len(h2s) else None

        # Get all elements between this h2 and next h2
        h3s_in_section = _find_h3s_between(tree, h2, next_h2)

        if h3s_in_section:
            # Collection with individual poems
            for h3_idx, h3 in enumerate(h3s_in_section):
                title = _clean_title(lhtml.tostring(h3, encoding="unicode"))
                if not title or len(title) < 2:
                    continue

                # Get content between this h3 and next h3 (or end of section)
                next_h3 = h3s_in_section[h3_idx + 1] if h3_idx + 1 < len(h3s_in_section) else next_h2
                content = _get_content_between(tree, h3, next_h3)

                if content is not None:
                    stanzas = _extract_stanzas(content)
                else:
                    stanzas = []

                if stanzas:
                    n_lines = sum(len(s) for s in stanzas)
                    total_lines += n_lines
                    poem_dict = {
                        "title": title,
                        "stanzas": [
                            {"stanza_num": "", "lines": st}
                            for st in stanzas
                        ],
                    }
                    # Tag Ring and the Book poems with speaker
                    if is_ring:
                        h3_id = h3.get("id", "")
                        for prefix, speaker in RING_SPEAKERS.items():
                            if h3_id.startswith(prefix):
                                poem_dict["speaker"] = speaker
                                break
                    poems.append(poem_dict)
        else:
            # Standalone long poem (Prince Hohenstiel, Inn Album, etc.)
            content = _get_content_between(tree, h2, next_h2)
            if content is not None:
                stanzas = _extract_stanzas(content)
                if stanzas:
                    title = _clean_title(lhtml.tostring(h2, encoding="unicode"))
                    n_lines = sum(len(s) for s in stanzas)
                    total_lines += n_lines
                    poems.append({
                        "title": title,
                        "stanzas": [
                            {"stanza_num": "", "lines": st}
                            for st in stanzas
                        ],
                    })

        print(f"    {section_name[:60]:60s} → {sum(1 for p in poems if True)} poems so far")

    print(f"\n  {sections_used} sections, {len(poems)} poems, {total_lines} lines")

    output = {
        "tcp_id": "",
        "gutenberg_id": "50954",
        "author": "Robert Browning",
        "title": "Complete Poetic and Dramatic Works (selected)",
        "date": "1868",
        "source": "Gutenberg",
        "poems": poems,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"  Written: {OUT_PATH}")


# --- Document-order helpers (sourceline overflows at 65535 in lxml) ---

from copy import deepcopy
from lxml import etree


def _build_doc_order(tree):
    """Assign _doc_pos attribute to every element for document-order comparison."""
    for i, el in enumerate(tree.iter()):
        el.set("data-doc-pos", str(i))


def _doc_pos(el):
    """Get document-order position from data attribute."""
    return int(el.get("data-doc-pos", "0"))


def _find_h3s_between(tree, h2, next_h2):
    """Find all h3 and h5 poem-title elements between two h2 elements.

    Dramatic Lyrics uses h5 for individual poems within h3 groups.
    We merge both and sort by document order.
    """
    h2_pos = _doc_pos(h2)
    next_pos = _doc_pos(next_h2) if next_h2 is not None else float("inf")

    headings = []
    for tag in ("h3", "h5"):
        for el in tree.xpath(f"//{tag}"):
            pos = _doc_pos(el)
            if h2_pos < pos < next_pos:
                headings.append((pos, el))

    headings.sort(key=lambda x: x[0])
    return [el for _, el in headings]


def _get_content_between(tree, start_el, end_el):
    """Create a virtual container with copies of poetry elements between start and end.

    Only collects top-level poetry containers to avoid duplication from
    nested structures like div.poetry-container > div.poetry.
    """
    start_pos = _doc_pos(start_el)
    end_pos = _doc_pos(end_el) if end_el is not None else float("inf")

    wrapper = lhtml.Element("div")
    skip_until = -1  # skip children of already-added containers

    for div in tree.iter("div"):
        pos = _doc_pos(div)
        if not (start_pos < pos < end_pos):
            if pos >= end_pos:
                break
            continue
        if pos <= skip_until:
            continue
        cls = div.get("class", "")
        if cls == "poem" or "poetry" in cls:
            wrapper.append(deepcopy(div))
            # Skip all descendants of this div to avoid double-counting
            # Find the max pos of any descendant
            max_child = pos
            for desc in div.iter():
                dp = _doc_pos(desc)
                if dp > max_child:
                    max_child = dp
            skip_until = max_child

    if len(wrapper) == 0:
        return None
    return wrapper


if __name__ == "__main__":
    parse()
