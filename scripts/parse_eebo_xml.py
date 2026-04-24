#!/usr/bin/env python3
"""
parse_eebo_xml.py — Parse EEBO-TCP TEI-XML files into structured intermediate JSON.

Handles the TEI namespace, extracts poetry (not prose/drama), preserves
poem/stanza/line hierarchy, and logs gaps for later patching.

Usage:
    python scripts/parse_eebo_xml.py                    # Parse all XMLs in corpus/raw/
    python scripts/parse_eebo_xml.py corpus/raw/A03058.xml  # Parse a single file
"""

import json
import os
import re
import sys
from pathlib import Path

import yaml
from lxml import etree

TEI_NS = "http://www.tei-c.org/ns/1.0"
NS = {"tei": TEI_NS}

# div types we treat as poetry containers
POETRY_DIV_TYPES = {
    "poem", "poems", "sonnet", "sonnets", "song", "songs", "elegy",
    "elegies", "epigram", "epigrams", "ode", "odes", "hymn", "hymns",
    "eclogue", "eclogues", "epistle", "epistles", "satire", "satires",
    "verse", "verses", "collection_of_poems", "pastoral", "epitaph",
    "epitaphs", "canzone", "madrigal", "ballad",
}

# div types to always skip
SKIP_DIV_TYPES = {
    "play", "masque", "comedy", "tragedy", "interlude",
    "table_of_contents", "index", "errata", "imprimatur",
    "license", "privilege",
}


def load_config(config_path: str = "config/config.yaml") -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def extract_metadata(tree: etree._ElementTree) -> dict:
    """Extract basic metadata from teiHeader."""
    root = tree.getroot()

    def find_text(xpath: str) -> str:
        el = root.find(xpath, NS)
        return el.text.strip() if el is not None and el.text else ""

    # Author — try multiple locations
    author = (
        find_text(".//tei:teiHeader//tei:titleStmt/tei:author")
        or find_text(".//tei:teiHeader//tei:sourceDesc//tei:author")
        or ""
    )

    title = find_text(".//tei:teiHeader//tei:titleStmt/tei:title")

    # Date — prefer the original publication date in sourceDesc (not the TCP digitisation date)
    date = (
        find_text(".//tei:teiHeader//tei:sourceDesc//tei:publicationStmt/tei:date")
        or find_text(".//tei:teiHeader//tei:editionStmt//tei:date")
        or ""
    )

    # TCP ID from the IDno
    tcp_id = ""
    for idno in root.findall(".//tei:teiHeader//tei:publicationStmt/tei:idno", NS):
        idno_type = idno.get("type", "")
        if idno_type in ("DLPS", "STC", "TCP", "EEBO"):
            tcp_id = (idno.text or "").strip()
            if tcp_id.startswith("A") or tcp_id.startswith("B"):
                break

    # Fallback: extract from filename-ish identifiers
    if not tcp_id:
        for idno in root.findall(".//tei:teiHeader//tei:publicationStmt/tei:idno", NS):
            text = (idno.text or "").strip()
            if re.match(r"^[AB]\d{5}$", text):
                tcp_id = text
                break

    return {
        "tcp_id": tcp_id,
        "author": author,
        "title": title,
        "date": date,
    }


def extract_line_text(line_el: etree._Element) -> str:
    """
    Extract plain text from a <l> element, handling:
    - <g ref="char:EOLhyphen"/> — join hyphenated words
    - <seg rend="decorInit"> — merge with following text
    - <hi> — keep text, drop markup
    - <gap> — replace with [...]
    - <note> — drop entirely (footnotes)
    - All other inline elements — extract text content
    """
    parts = []

    def walk(el, drop_tail_after_gap=False):
        tag = etree.QName(el.tag).localname if isinstance(el.tag, str) else ""

        if tag == "gap":
            parts.append("[...]")
            # Still pick up tail text after the gap
            if el.tail:
                parts.append(el.tail)
            return

        if tag == "note":
            # Drop notes entirely but keep tail
            if el.tail:
                parts.append(el.tail)
            return

        if tag == "g":
            ref = el.get("ref", "")
            if "EOLhyphen" in ref:
                # Remove trailing whitespace/hyphen from the last part
                if parts:
                    parts[-1] = parts[-1].rstrip().rstrip("-")
            if el.tail:
                parts.append(el.tail)
            return

        # For everything else: take text, recurse children, take tail
        if el.text:
            parts.append(el.text)
        for child in el:
            walk(child)
        if el.tail:
            parts.append(el.tail)

    # Start with the <l> element itself
    if line_el.text:
        parts.append(line_el.text)
    for child in line_el:
        walk(child)

    text = "".join(parts)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Normalize long-s (ECCO-TCP preserves it verbatim; hurts retrieval)
    text = text.replace("ſ", "s")
    return text


def extract_gaps_from_line(line_el: etree._Element, context_text: str) -> list[dict]:
    """Extract gap information from a <l> element."""
    gaps = []
    for gap_el in line_el.findall(".//tei:gap", NS):
        extent = gap_el.get("extent", "unknown")
        reason = gap_el.get("reason", "unknown")
        resp = gap_el.get("resp", "")
        gaps.append({
            "extent": extent,
            "reason": reason,
            "resp": resp,
            "context": context_text[:80] if context_text else "",
        })
    return gaps


def is_verse_div(div: etree._Element) -> bool:
    """
    Heuristic: does this div contain verse (lg/l elements) rather than prose (p elements)?
    """
    lg_count = len(div.findall(".//tei:lg", NS)) + len(div.findall(".//tei:l", NS))
    p_count = len(div.findall("tei:p", NS))  # Direct children only

    # If it has line groups or lines, treat as verse
    if lg_count > 0:
        return True
    # If it only has prose paragraphs, skip
    if p_count > 0 and lg_count == 0:
        return False
    return False


def extract_stanzas_from_lg(lg_el: etree._Element) -> dict:
    """Extract a stanza from a <lg> element."""
    stanza_num = lg_el.get("n", "")
    lines = []
    gaps = []

    for line_el in lg_el.findall("tei:l", NS):
        text = extract_line_text(line_el)
        if text:
            lines.append(text)
            line_gaps = extract_gaps_from_line(line_el, text)
            for g in line_gaps:
                g["line_num"] = len(lines)
            gaps.extend(line_gaps)

    return {
        "stanza_num": stanza_num,
        "lines": lines,
        "gaps": gaps,
    }


def extract_poem(div: etree._Element) -> dict | None:
    """
    Extract a poem from a <div> element.
    Returns poem dict or None if no verse content found.
    """
    div_type = div.get("type", "")

    # Try to get poem title from <head>.
    # Titles often wrap italics in <hi>: "Of <hi>Aire.</hi>" — must use itertext()
    # unconditionally; head.text only returns text *before* the first child.
    head = div.find("tei:head", NS)
    poem_title = ""
    if head is not None:
        poem_title = "".join(head.itertext()).strip()
        # Collapse multi-line / doubled whitespace from element boundaries
        poem_title = re.sub(r"\s+", " ", poem_title)
        # Normalize long-s
        poem_title = poem_title.replace("ſ", "s")

    stanzas = []

    # First, look for <lg> children (stanza groups)
    lgs = div.findall("tei:lg", NS)
    if lgs:
        for lg in lgs:
            # An <lg> might contain sub-<lg> elements (stanzas within a section)
            sub_lgs = lg.findall("tei:lg", NS)
            if sub_lgs:
                for sub_lg in sub_lgs:
                    stanza = extract_stanzas_from_lg(sub_lg)
                    if stanza["lines"]:
                        stanzas.append(stanza)
            else:
                stanza = extract_stanzas_from_lg(lg)
                if stanza["lines"]:
                    stanzas.append(stanza)
    else:
        # No <lg> — look for bare <l> elements directly in this div
        lines_els = div.findall("tei:l", NS)
        if lines_els:
            lines = []
            gaps = []
            for line_el in lines_els:
                text = extract_line_text(line_el)
                if text:
                    lines.append(text)
                    line_gaps = extract_gaps_from_line(line_el, text)
                    for g in line_gaps:
                        g["line_num"] = len(lines)
                    gaps.extend(line_gaps)
            if lines:
                stanzas.append({
                    "stanza_num": "",
                    "lines": lines,
                    "gaps": gaps,
                })

    if not stanzas:
        return None

    return {
        "title": poem_title,
        "div_type": div_type,
        "stanzas": stanzas,
    }


def extract_poems_from_div(div: etree._Element, depth: int = 0) -> list[dict]:
    """
    Recursively extract poems from a div and its children.
    Handles nested div structures common in EEBO-TCP.
    """
    poems = []
    div_type = (div.get("type") or "").lower()

    # Skip non-poetry divs
    if div_type in SKIP_DIV_TYPES:
        return poems

    # Check for direct poetry div types
    if div_type in POETRY_DIV_TYPES or is_verse_div(div):
        # Check if this div has sub-divs that are individual poems
        sub_divs = div.findall("tei:div", NS)
        if sub_divs:
            for sub_div in sub_divs:
                poems.extend(extract_poems_from_div(sub_div, depth + 1))
        else:
            poem = extract_poem(div)
            if poem:
                poems.append(poem)
    elif div_type == "book":
        # Books (e.g. Paradise Lost) — check if verse
        if is_verse_div(div):
            poem = extract_poem(div)
            if poem:
                poems.append(poem)
        # Also recurse into sub-divs
        for sub_div in div.findall("tei:div", NS):
            poems.extend(extract_poems_from_div(sub_div, depth + 1))
    else:
        # Recurse into sub-divs looking for poetry
        for sub_div in div.findall("tei:div", NS):
            poems.extend(extract_poems_from_div(sub_div, depth + 1))

    return poems


def handle_speaker_lines(div: etree._Element) -> list[dict]:
    """Handle <sp><speaker> elements in dialogue poems (e.g., Marvell)."""
    poems = []

    for sp in div.findall(".//tei:sp", NS):
        speaker_el = sp.find("tei:speaker", NS)
        speaker = ""
        if speaker_el is not None:
            speaker = "".join(speaker_el.itertext()).strip()

        # Get lines from lg or bare l elements within the speech
        for lg in sp.findall("tei:lg", NS):
            stanza = extract_stanzas_from_lg(lg)
            if stanza["lines"] and speaker:
                stanza["lines"][0] = f"[{speaker}] {stanza['lines'][0]}"
            if stanza["lines"]:
                poems.append(stanza)

        # Bare lines in the speech
        bare_lines = sp.findall("tei:l", NS)
        if bare_lines:
            lines = []
            for line_el in bare_lines:
                text = extract_line_text(line_el)
                if text:
                    lines.append(text)
            if lines and speaker:
                lines[0] = f"[{speaker}] {lines[0]}"
            if lines:
                poems.append({
                    "stanza_num": "",
                    "lines": lines,
                    "gaps": [],
                })

    return poems


def parse_eebo_xml(xml_path: str) -> dict:
    """
    Parse a single EEBO-TCP XML file into structured intermediate JSON.
    """
    tree = etree.parse(xml_path)
    metadata = extract_metadata(tree)
    root = tree.getroot()

    # Find the body
    body = root.find(".//tei:text/tei:body", NS)
    if body is None:
        return {**metadata, "source": "EEBO-TCP", "poems": [], "gap_log": []}

    # Extract poems from all top-level divs
    poems = []
    for div in body.findall("tei:div", NS):
        poems.extend(extract_poems_from_div(div))

    # Also check for poems in <front> and <back> that might be verse dedications
    for section_name in ["front", "back"]:
        section = root.find(f".//tei:text/tei:{section_name}", NS)
        if section is not None:
            for div in section.findall("tei:div", NS):
                div_type = (div.get("type") or "").lower()
                # Only extract clearly poetic front/back matter
                if div_type in POETRY_DIV_TYPES:
                    poems.extend(extract_poems_from_div(div))

    # Build gap log
    gap_log = []
    for poem in poems:
        for i, stanza in enumerate(poem.get("stanzas", [])):
            for gap in stanza.get("gaps", []):
                gap_log.append({
                    "poem": poem.get("title", "untitled"),
                    "stanza": i + 1,
                    "line": gap.get("line_num", "?"),
                    "extent": gap.get("extent", "unknown"),
                    "reason": gap.get("reason", "unknown"),
                    "context": gap.get("context", ""),
                })

    return {
        **metadata,
        "source": "EEBO-TCP",
        "poems": poems,
        "gap_log": gap_log,
    }


def parse_all(raw_dir: str, output_dir: str) -> None:
    """Parse all XML files in the raw directory."""
    os.makedirs(output_dir, exist_ok=True)
    xml_files = sorted(Path(raw_dir).glob("*.xml"))

    if not xml_files:
        print(f"No XML files found in {raw_dir}")
        return

    total_poems = 0
    total_gaps = 0

    for xml_path in xml_files:
        tcp_id = xml_path.stem
        print(f"  Parsing {tcp_id} ...", end=" ", flush=True)

        try:
            result = parse_eebo_xml(str(xml_path))
            poem_count = len(result["poems"])
            gap_count = len(result["gap_log"])
            total_poems += poem_count
            total_gaps += gap_count

            out_path = os.path.join(output_dir, f"{tcp_id}.json")
            with open(out_path, "w") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            print(f"{poem_count} poems, {gap_count} gaps")
        except Exception as e:
            print(f"FAILED: {e}")

    print(f"\nTotal: {len(xml_files)} files, {total_poems} poems, {total_gaps} gaps")


def main():
    config = load_config()
    raw_dir = config["paths"]["corpus_raw"]
    intermediate_dir = config["paths"]["intermediate"]

    if len(sys.argv) > 1 and sys.argv[1] != "--all":
        # Parse a single file
        xml_path = sys.argv[1]
        result = parse_eebo_xml(xml_path)

        tcp_id = Path(xml_path).stem
        os.makedirs(intermediate_dir, exist_ok=True)
        out_path = os.path.join(intermediate_dir, f"{tcp_id}.json")
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        print(f"Parsed {xml_path}:")
        print(f"  Author: {result['author']}")
        print(f"  Title:  {result['title']}")
        print(f"  Date:   {result['date']}")
        print(f"  Poems:  {len(result['poems'])}")
        print(f"  Gaps:   {len(result['gap_log'])}")
        print(f"  Output: {out_path}")

        # Show first few poem titles
        for poem in result["poems"][:10]:
            stanza_count = len(poem.get("stanzas", []))
            line_count = sum(len(s["lines"]) for s in poem.get("stanzas", []))
            print(f"    - {poem['title'] or '(untitled)'}: {stanza_count} stanzas, {line_count} lines")
        if len(result["poems"]) > 10:
            print(f"    ... and {len(result['poems']) - 10} more")
    else:
        parse_all(raw_dir, intermediate_dir)


if __name__ == "__main__":
    main()
