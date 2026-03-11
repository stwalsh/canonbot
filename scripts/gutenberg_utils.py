"""
gutenberg_utils.py — Shared extraction utilities for Gutenberg poetry HTML.

Three markup patterns recur across Gutenberg:

  Pattern A ("noindent"):  <p class="noindent"> with <br/> line separators
  Pattern B ("pre"):       <pre> blocks with plain text, newline-separated
  Pattern C ("structured"): div.poem > div.stanza > span.i0/i2/i4 (modern Gutenberg)

This module provides extractors for each, plus common cleanup functions.
All extractors return the same shape: list of stanzas, each a list of line strings.
"""

import re
from html import unescape

from lxml import html as lhtml


# ---------------------------------------------------------------------------
# Common cleanup
# ---------------------------------------------------------------------------


def clean_line(text: str) -> str:
    """Normalize a single line of verse."""
    text = text.replace("\xa0", " ")
    text = text.rstrip()
    # Strip trailing line numbers (bare digits at end of line)
    text = re.sub(r"\s+\d+\s*$", "", text)
    return text


def clean_title(text: str) -> str:
    """Normalize a poem title from HTML heading."""
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).strip()
    text = re.sub(r"\s+", " ", text)
    # Remove trailing periods (common in Gutenberg headings)
    text = re.sub(r"\.\s*$", "", text)
    return text


def lines_to_stanzas(lines: list[str]) -> list[list[str]]:
    """Split a flat list of lines (with blank-line stanza breaks) into stanzas."""
    stanzas = []
    current = []
    for line in lines:
        if not line.strip():
            if current:
                stanzas.append(current)
                current = []
        else:
            current.append(line)
    if current:
        stanzas.append(current)
    return stanzas


def stanzas_to_poem_dict(title: str, stanzas: list[list[str]]) -> dict:
    """Build intermediate JSON poem dict from title + stanzas."""
    return {
        "title": title,
        "stanzas": [
            {"stanza_num": "", "lines": st, "gaps": []}
            for st in stanzas
        ],
    }


def strip_gutenberg_boilerplate(html_str: str) -> str:
    """Remove Gutenberg header/footer from HTML string."""
    # Try to find content between START/END markers
    start = re.search(r"\*\*\* ?START OF.*?\*\*\*", html_str)
    end = re.search(r"\*\*\* ?END OF", html_str)
    if start:
        html_str = html_str[start.end():]
    if end:
        html_str = html_str[:end.start()]
    return html_str


# ---------------------------------------------------------------------------
# Pattern A: <p class="noindent"> + <br/>
# ---------------------------------------------------------------------------


def extract_stanzas_noindent(container) -> list[list[str]]:
    """Extract stanzas from a container using Pattern A.

    Each <p class="noindent"> is one stanza; lines separated by <br/>.
    Skips editorial paragraphs (no class, or class="footnote"/"poem"/"letter").
    """
    stanzas = []
    for p in container.findall(".//p"):
        cls = p.get("class", "")
        if cls not in ("noindent", "p2"):
            continue

        inner = lhtml.tostring(p, encoding="unicode")
        # Remove wrapping <p>
        inner = re.sub(r"^<p[^>]*>", "", inner)
        inner = re.sub(r"</p>\s*$", "", inner)
        # Split on <br>
        parts = re.split(r"<br\s*/?\s*>", inner)

        lines = []
        for part in parts:
            text = re.sub(r"<[^>]+>", "", part)
            text = unescape(text)
            text = clean_line(text)
            if text.strip():
                lines.append(text)
        # Skip very short fragments (editorial connectives like "or", "and")
        if lines and not (len(lines) == 1 and len(lines[0]) < 5):
            stanzas.append(lines)
    return stanzas


# ---------------------------------------------------------------------------
# Pattern B: <pre> plaintext
# ---------------------------------------------------------------------------


def extract_stanzas_pre(pre_element, strip_numbering=True) -> list[list[str]]:
    """Extract stanzas from a single <pre> block using Pattern B.

    Lines are newline-separated plain text. Stanzas separated by blank lines.
    If strip_numbering, removes stanza/section number lines (bare "1.", "II.", etc.).
    """
    text = pre_element.text_content()
    text = unescape(text)

    raw_lines = text.split("\n")
    cleaned = []
    for line in raw_lines:
        line = clean_line(line)
        # Optionally strip bare numbering lines
        if strip_numbering and re.match(r"^\s*\d+\.\s*$", line.strip()):
            cleaned.append("")  # treat as stanza break
            continue
        cleaned.append(line)

    return lines_to_stanzas(cleaned)


def extract_stanzas_all_pre(doc, skip_empty=True) -> list[list[list[str]]]:
    """Extract stanzas from ALL <pre> blocks in a document.

    Returns list of (stanzas_per_pre_block).
    """
    results = []
    for pre in doc.findall(".//pre"):
        stanzas = extract_stanzas_pre(pre)
        if skip_empty and not stanzas:
            continue
        results.append(stanzas)
    return results


# ---------------------------------------------------------------------------
# Pattern C: div.poem > div.stanza > span.i*
# ---------------------------------------------------------------------------


def extract_stanzas_structured(poem_div) -> list[list[str]]:
    """Extract stanzas from a structured poem div using Pattern C.

    div.poem > div.stanza > span.i0/i2/i4 (verse lines).
    Strips pagenum spans, linenum spans, and stanza-number-only stanzas.
    """
    stanzas = []

    for stanza_div in poem_div.findall(".//div[@class='stanza']"):
        # Remove junk spans before extraction
        for junk_cls in ("linenum", "pagenum"):
            for junk in stanza_div.findall(f".//span[@class='{junk_cls}']"):
                junk.getparent().remove(junk)

        lines = []
        for span in stanza_div.findall("./span"):
            cls = span.get("class", "")
            # Only take verse-line spans (i0, i1, i2, i4, etc.)
            if not re.match(r"i\d", cls):
                continue
            text = span.text_content().strip()
            if text:
                text = clean_line(text)
                text = text.replace("\xa0", " ")
                text = re.sub(r"\s+", " ", text).strip()
                # Skip bare stanza/section numbers
                if re.match(r"^\d+\.?\s*$", text):
                    continue
                lines.append(text)
        if lines:
            stanzas.append(lines)

    return stanzas


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------


def detect_pattern(doc) -> str:
    """Guess which markup pattern a Gutenberg HTML doc uses.

    Returns "structured", "pre", or "noindent".
    """
    if doc.findall(".//div[@class='poem']"):
        return "structured"
    pre_count = len(doc.findall(".//pre"))
    noindent_count = len(doc.findall(".//p[@class='noindent']"))
    if pre_count > noindent_count:
        return "pre"
    return "noindent"
