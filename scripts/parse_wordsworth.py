#!/usr/bin/env python3
"""
parse_wordsworth.py — Parse Wordsworth from Gutenberg HTML into intermediate JSON.

Sources (up to and including 1807 Poems in Two Volumes):
  - #9622:  Lyrical Ballads 1798 — Pattern A (p.noindent + br)
  - #8912:  Lyrical Ballads 1800, Volume 2 — plain <p> + <br>, poorly structured
  - #8774:  Poems in Two Volumes 1807, Volume 1 — plain <p> + <br>
  - #8824:  Poems in Two Volumes 1807, Volume 2 — plain <p> + <br>

Skipped:
  - #8905 (LB 1800 vol 1): reprints LB 1798 poems, adds nothing new for Wordsworth
  - 1805 Prelude: not on Gutenberg (separate parser needed for Oxford TEI-XML source)

Usage:
    python scripts/parse_wordsworth.py              # Fetch if needed, parse all
    python scripts/parse_wordsworth.py --cached     # Use cached HTML
"""

import json
import os
import re
import sys
import urllib.request
from html import unescape
from pathlib import Path

from lxml import html as lhtml

from gutenberg_utils import (
    clean_line,
    clean_title,
    extract_stanzas_noindent,
    lines_to_stanzas,
    stanzas_to_poem_dict,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GUTENBERG_FILES = {
    "wordsworth_lb1798.html": ("https://www.gutenberg.org/files/9622/9622-h/9622-h.htm", "9622"),
    "wordsworth_lb1800v2.html": ("https://www.gutenberg.org/cache/epub/8912/pg8912-images.html", "8912"),
    "wordsworth_poems1807v1.html": ("https://www.gutenberg.org/cache/epub/8774/pg8774-images.html", "8774"),
    "wordsworth_poems1807v2.html": ("https://www.gutenberg.org/cache/epub/8824/pg8824-images.html", "8824"),
}
RAW_DIR = Path("corpus/raw/gutenberg")
INTERMEDIATE_DIR = Path("corpus/intermediate")

POET = "William Wordsworth"

# Coleridge poems in 1798 Lyrical Ballads — exclude
COLERIDGE_POEMS = {
    "THE RIME OF THE ANCYENT MARINERE",
    "THE FOSTER-MOTHER",   # "THE FOSTER-MOTHER'S TALE" — curly quotes vary
    "THE NIGHTINGALE",
    "THE DUNGEON",
}

# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def ensure_downloaded():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name, (url, _) in GUTENBERG_FILES.items():
        path = RAW_DIR / name
        if path.exists():
            print(f"  {name}: cached")
            continue
        print(f"  Fetching {name}...")
        req = urllib.request.Request(
            url, headers={"User-Agent": "CanonBot/1.0 (poetry corpus)"}
        )
        resp = urllib.request.urlopen(req, timeout=30)
        data = resp.read()
        path.write_bytes(data)
        print(f"    {len(data):,} bytes")


# ---------------------------------------------------------------------------
# Shared: extract stanzas from plain <p> + <br> (Pattern A variant, no class)
# ---------------------------------------------------------------------------


def _extract_verse_from_p(p_el) -> list[str]:
    """Extract lines from a <p> element with <br> separators.

    Returns a flat list of lines (stanza breaks are NOT preserved).
    """
    raw = lhtml.tostring(p_el, encoding="unicode")
    raw = re.sub(r"^<p[^>]*>", "", raw)
    raw = re.sub(r"</p>\s*$", "", raw)
    parts = re.split(r"<br\s*/?\s*>", raw)
    lines = []
    for part in parts:
        text = re.sub(r"<[^>]+>", "", part)
        text = unescape(text)
        text = clean_line(text)
        if text.strip():
            lines.append(text)
    return lines


def _extract_stanzas_from_p(p_el) -> list[list[str]]:
    """Extract stanzas from a <p> element, splitting on double <br>.

    Some Gutenberg volumes pack all stanzas into one <p> with <br><br>
    between stanzas. This function splits on that boundary.
    """
    raw = lhtml.tostring(p_el, encoding="unicode")
    raw = re.sub(r"^<p[^>]*>", "", raw)
    raw = re.sub(r"</p>\s*$", "", raw)
    # Split on double <br> (stanza boundary)
    stanza_chunks = re.split(r"<br\s*/?\s*>\s*<br\s*/?\s*>", raw)
    stanzas = []
    for chunk in stanza_chunks:
        parts = re.split(r"<br\s*/?\s*>", chunk)
        lines = []
        for part in parts:
            text = re.sub(r"<[^>]+>", "", part)
            text = unescape(text)
            text = clean_line(text)
            if text.strip():
                lines.append(text)
        if lines:
            stanzas.append(lines)
    return stanzas


def _is_verse_p(p_el) -> bool:
    """Check if a <p> element contains verse (has <br> tags)."""
    raw = lhtml.tostring(p_el, encoding="unicode")
    return "<br" in raw


def _is_title_p(p_el) -> bool:
    """Check if a <p> element looks like a poem title (short, no <br>)."""
    raw = lhtml.tostring(p_el, encoding="unicode")
    if "<br" in raw:
        return False
    text = p_el.text_content().strip()
    if not text or len(text) > 120:
        return False
    # Footnotes
    if text.startswith("[Footnote") or text.startswith("[Transcriber"):
        return False
    return True


# ---------------------------------------------------------------------------
# Lyrical Ballads 1798 (#9622) — Pattern A
# ---------------------------------------------------------------------------

LB1798_SKIP = {"ADVERTISEMENT.", "CONTENTS."}


def parse_lb1798() -> list[dict]:
    """Parse Lyrical Ballads 1798. Pattern A: p.noindent + br."""
    path = RAW_DIR / "wordsworth_lb1798.html"
    doc = lhtml.fromstring(path.read_bytes())

    poems = []
    h2s = doc.findall(".//h2")

    for h2 in h2s:
        heading = h2.text_content().strip().replace("\n", " ")
        heading = re.sub(r"\s+", " ", heading)

        if heading in LB1798_SKIP:
            continue

        # Skip Coleridge poems
        heading_upper = heading.upper().rstrip("., ;")
        is_coleridge = False
        for c in COLERIDGE_POEMS:
            if heading_upper.startswith(c[:25]):
                is_coleridge = True
                break
        if is_coleridge:
            continue

        # Collect p.noindent stanzas between this h2 and the next
        all_stanzas = []
        el = h2.getnext()
        while el is not None:
            if el.tag == "h2":
                break
            if el.tag == "p" and el.get("class", "") in ("noindent", "p2"):
                # Use stanza-aware extraction (splits on double <br>)
                stanzas = _extract_stanzas_from_p(el)
                for st in stanzas:
                    if st and not (len(st) == 1 and len(st[0]) < 5):
                        all_stanzas.append(st)
            el = el.getnext()

        if not all_stanzas:
            continue

        # Clean up title
        title = _normalize_lb_title(heading)
        poems.append(stanzas_to_poem_dict(title, all_stanzas))

    return poems


LB1798_TITLE_MAP = {
    "LINES WRITTEN A FEW MILES ABOVE TINTERN ABBEY": "Lines Composed a Few Miles Above Tintern Abbey",
}


def _normalize_lb_title(heading: str) -> str:
    """Normalize a Lyrical Ballads title."""
    # Remove trailing period
    title = heading.rstrip(".")

    # Check explicit title map first (prefix matching)
    for prefix, mapped in LB1798_TITLE_MAP.items():
        if title.upper().startswith(prefix):
            return mapped

    # Title-case ALL CAPS or mostly-CAPS titles
    upper_chars = sum(1 for c in title if c.isupper())
    lower_chars = sum(1 for c in title if c.islower())
    if upper_chars > lower_chars and len(title) > 3:
        title = _title_case(title)
    return title


# ---------------------------------------------------------------------------
# Lyrical Ballads 1800, Volume 2 (#8912) — poorly structured
# ---------------------------------------------------------------------------

# Poems in lb1800v2, detected by first-line fingerprint or title paragraph.
# Maps fingerprint (first N chars of first line) → display title.
LB1800V2_FINGERPRINTS = {
    "The Knight had ridden down from Wensley": "Hart-Leap Well",
    "There was a Boy, ye knew him well": "There Was a Boy",
    "These Tourists, Heaven preserve us": "The Brothers",
    "Fair Ellen Irwin, when she sate": "Ellen Irwin",
    "Strange fits of passion I have known": "Strange Fits of Passion Have I Known",
    "She dwelt among th": "She Dwelt Among the Untrodden Ways",
    "I travell\u2019d among unknown Men": "I Travelled Among Unknown Men",
    "I travell'd among unknown Men": "I Travelled Among Unknown Men",
    "A slumber did my spirit seal": "A Slumber Did My Spirit Seal",
    "Three years she grew in sun and shower": "Three Years She Grew",
    "Oft I had heard of Lucy Gray": "Lucy Gray",
    "If from the public way you turn your steps": "Michael",
    "Between two sister moorland rills": "The Danish Boy",
    "It was an April Morning: fresh and clear": "Poems on the Naming of Places, I",
    "Amid the smoke of cities did you pass": "To Joanna",
    "There is an Eminence,\u2014of these our hills": "Poems on the Naming of Places, III",
    "There is an Eminence,—of these our hills": "Poems on the Naming of Places, III",
    "A narrow girdle of rough stones and crags": "Poems on the Naming of Places, IV",
    "Our walk was far among the ancient trees": "To M.H.",
    # Unlabelled poems between To a Sexton and Ruth
    "I hate that Andrew Jones": "Andrew Jones",
    "A whirl-blast from behind the hill": "A Whirl-Blast from Behind the Hill",
    "A whirl\u2010blast from behind the hill": "A Whirl-Blast from Behind the Hill",
    "Though the torrents from their fountains": "Song for the Wandering Jew",
    "I saw an aged Beggar in my walk": "The Old Cumberland Beggar",
    "There\u2019s George Fisher, Charles Fleming": "The Brothers [Rural Architecture]",
    "There's George Fisher, Charles Fleming": "Rural Architecture",
    "Art thou a Statesman, in the van": "A Poet's Epitaph",
    # We talked and laughed — The Fountain
    "We talk\u2019d with open heart": "The Fountain",
    "We talked with open heart": "The Fountain",
    "We talk'd with open heart": "The Fountain",
}

# Title paragraphs in lb1800v2 (text of <p> → display title)
LB1800V2_TITLE_PARAS = {
    "The WATERFALL and the EGLANTINE.": "The Waterfall and the Eglantine",
    "The OAK and the BROOM,": "The Oak and the Broom",
    "The IDLE SHEPHERD-BOYS,": "The Idle Shepherd-Boys",
    "To a SEXTON.": "To a Sexton",
    "The Two April Mornings.": "The Two April Mornings",
    "The Fountain": "The Fountain",
    "The Pet-Lamb, A Pastoral.": "The Pet-Lamb",
    "The CHILDLESS FATHER.": "The Childless Father",
    "POEMS on the NAMING of PLACES.": None,  # Section header, not a poem
    "The BROTHERS. [1]": None,  # Subtitle for The Brothers (already detected)
}

# Headings to skip
LB1800V2_SKIP_H = {
    "The Project Gutenberg eBook of Lyrical Ballads with Other Poems, 1800, Volume 2",
    "THE FULL PROJECT GUTENBERG LICENSE",
    "ADVERTISEMENT.",
    "ADVERTISEMENT",
}

# Numbered section titles in "Poems on the Naming of Places"
LB1800V2_NUMBERED_TITLES = {
    "1.": "Poems on the Naming of Places, I",
    "2.": None,  # already caught by fingerprint (To Joanna)
}


def parse_lb1800v2() -> list[dict]:
    """Parse Lyrical Ballads 1800, Volume 2. Messy markup."""
    path = RAW_DIR / "wordsworth_lb1800v2.html"
    doc = lhtml.fromstring(path.read_bytes())
    body = doc.find(".//body")
    if body is None:
        return []

    poems = []
    current_title = None
    current_stanzas = []
    in_notes = False

    def _flush():
        nonlocal current_title, current_stanzas
        if current_title and current_stanzas:
            poems.append(stanzas_to_poem_dict(current_title, current_stanzas))
        current_title = None
        current_stanzas = []

    for el in body:
        if el.tag in ("h2", "h3"):
            text = el.text_content().strip().replace("\n", " ")
            text_clean = re.sub(r"\s+", " ", text)

            if text_clean in LB1800V2_SKIP_H:
                continue

            # h3 "PART SECOND." is a section within Hart-Leap Well, not a new poem
            if "PART SECOND" in text_clean:
                continue

            # h2 "THE" is the broken heading for "The Brothers"
            if text_clean == "THE":
                continue

            # h2 "RUTH." starts Ruth
            if text_clean.startswith("RUTH"):
                _flush()
                current_title = "Ruth"
                current_stanzas = []
                continue

            # Roman numeral section headers (I., II., etc.) — Poems on Naming of Places
            if re.match(r"^[IVXLC]+\.?\s*$", text_clean):
                continue

            # If we get here, it's a poem heading
            _flush()
            current_title = clean_title(text_clean)
            current_stanzas = []
            continue

        if el.tag == "h4":
            text = el.text_content().strip()
            # Skip: VOL. II., CONTENTS, NOTE, numbered sections
            continue

        if el.tag != "p":
            continue

        text = el.text_content().strip()
        if not text:
            continue

        # Skip notes section
        if text.startswith("NOTES TO THE POEM"):
            in_notes = True
            continue
        if in_notes:
            continue

        # Skip footnotes and transcriber notes
        if text.startswith("[Footnote") or text.startswith("[Transcriber"):
            continue

        # Check if this is a verse paragraph
        if _is_verse_p(el):
            lines = _extract_verse_from_p(el)
            if not lines:
                continue

            first_line = lines[0].strip()

            # Check for inline title (e.g., "ELLEN IRWIN,\n   Or the BRAES of KIRTLE.")
            # These appear as first two lines of a verse paragraph
            if "ELLEN IRWIN" in first_line.upper():
                _flush()
                current_title = "Ellen Irwin"
                # Strip the title lines
                lines = [l for l in lines if not re.match(
                    r"^\s*(ELLEN IRWIN|Or the BRAES)", l, re.IGNORECASE)]
                if lines:
                    current_stanzas.append(lines)
                continue

            # Check for "A CHARACTER," inline title
            if first_line.startswith("A CHARACTER"):
                _flush()
                current_title = "A Character"
                lines = [l for l in lines if not re.match(r"^\s*A CHARACTER", l)]
                if lines:
                    current_stanzas.append(lines)
                continue

            # Check for "The TWO THIEVES," inline title
            if "TWO THIEVES" in first_line.upper():
                _flush()
                current_title = "The Two Thieves"
                lines = [l for l in lines if not re.match(
                    r"^\s*(The TWO THIEVES|Or the last Stage)", l, re.IGNORECASE)]
                if lines:
                    current_stanzas.append(lines)
                continue

            # Check for "The OLD CUMBERLAND BEGGAR," inline title
            if "OLD CUMBERLAND BEGGAR" in first_line.upper():
                _flush()
                current_title = "The Old Cumberland Beggar"
                lines = [l for l in lines if not re.match(
                    r"^\s*(The OLD CUMBERLAND|A DESCRIPTION)", l, re.IGNORECASE)]
                if lines:
                    current_stanzas.append(lines)
                continue

            # Check fingerprints
            matched = False
            for fingerprint, title in LB1800V2_FINGERPRINTS.items():
                if first_line.startswith(fingerprint[:35]):
                    _flush()
                    current_title = title
                    current_stanzas.append(lines)
                    matched = True
                    break

            if not matched:
                # Append to current poem
                if current_title:
                    current_stanzas.append(lines)

        elif _is_title_p(el):
            # Check if this is a known title paragraph
            text_stripped = text.rstrip(".")

            # Check title paragraph map
            for tp_text, tp_title in LB1800V2_TITLE_PARAS.items():
                if text.startswith(tp_text[:30]):
                    if tp_title is not None:
                        _flush()
                        current_title = tp_title
                        current_stanzas = []
                    matched = True
                    break

            # Numbered sections
            for num, num_title in LB1800V2_NUMBERED_TITLES.items():
                if text.strip() == num and num_title:
                    _flush()
                    current_title = num_title
                    current_stanzas = []
                    break

            # "To JOANNA." title
            if text.strip().startswith("To JOANNA"):
                _flush()
                current_title = "To Joanna"
                current_stanzas = []

            # "To M. H." title
            if text.strip().startswith("To M. H"):
                _flush()
                current_title = "To M.H."
                current_stanzas = []

    _flush()

    # Post-process: merge "The Fountain" properly
    # The Fountain is a dialogue poem; check it's not split

    return poems


# ---------------------------------------------------------------------------
# Poems in Two Volumes 1807, Volume 1 (#8774)
# ---------------------------------------------------------------------------

POEMS1807V1_SKIP = {
    "The Project Gutenberg eBook of Poems in Two Volumes, Volume 1",
    "CONTENTS",
    "THE FULL PROJECT GUTENBERG LICENSE",
    "END OF THE FIRST PART.",
    "END OF THE FIRST PART",
    "PART THE FIRST.",
    "PART THE FIRST",
}

# Section headers (not poem titles)
POEMS1807V1_SECTIONS = {
    "POEMS, COMPOSED DURING A TOUR, CHIEFLY ON FOOT.",
    "SONNETS",
    "PART THE SECOND.",
    "PART THE SECOND",
}

# Title normalization for 1807 v1
POEMS1807V1_TITLE_MAP = {
    "TO THE DAISY.": "To the Daisy",
    "LOUISA.": "Louisa",
    "FIDELITY.": "Fidelity",
    "THE SAILOR'S MOTHER.": "The Sailor's Mother",
    "TO THE SAME FLOWER.": "To the Same Flower [Daisy]",
    "THE HORN OF EGREMONT CASTLE.": "The Horn of Egremont Castle",
    "THE KITTEN AND THE FALLING LEAVES.": "The Kitten and the Falling Leaves",
    "ODE TO DUTY.": "Ode to Duty",
    "1. BEGGARS.": "Beggars",
    "2. TO A SKY-LARK.": "To a Sky-Lark",
    "5. RESOLUTION AND INDEPENDENCE.": "Resolution and Independence",
    "5. TO SLEEP.": "To Sleep [I]",
    "6. TO SLEEP.": "To Sleep [II]",
    "7. TO SLEEP.": "To Sleep [III]",
    "9. TO THE RIVER DUDDON.": "To the River Duddon",
    "10. FROM THE ITALIAN OF MICHAEL ANGELO.": "From the Italian of Michael Angelo [I]",
    "11. FROM THE SAME.": "From the Italian of Michael Angelo [II]",
    "12. FROM THE SAME.": "From the Italian of Michael Angelo [III]",
    "20. TO THE MEMORY OF RAISLEY CALVERT.": "To the Memory of Raisley Calvert",
    "6. ON THE EXTINCTION OF THE VENETIAN REPUBLIC.": "On the Extinction of the Venetian Republic",
    "7. THE KING OF SWEDEN.": "The King of Sweden",
    "8. TO TOUSSAINT L'OUVERTURE.": "To Toussaint L'Ouverture",
    "12. THOUGHT OF A BRITON ON THE SUBJUGATION OF SWITZERLAND.": "Thought of a Briton on the Subjugation of Switzerland",
    "23. TO THE MEN OF KENT.": "To the Men of Kent",
    "25. ANTICIPATION.": "Anticipation",
}

# h3 titles in 1807v1
POEMS1807V1_H3_TITLES = {
    "SHE WAS A PHANTOM OF DELIGHT": "She Was a Phantom of Delight",
    "4. ALICE FELL.": "Alice Fell",
}


def parse_poems1807v1() -> list[dict]:
    """Parse Poems in Two Volumes 1807, Volume 1."""
    path = RAW_DIR / "wordsworth_poems1807v1.html"
    doc = lhtml.fromstring(path.read_bytes())
    body = doc.find(".//body")
    if body is None:
        return []

    poems = []
    current_title = None
    current_stanzas = []
    in_notes = False

    def _flush():
        nonlocal current_title, current_stanzas
        if current_title and current_stanzas:
            poems.append(stanzas_to_poem_dict(current_title, current_stanzas))
        current_title = None
        current_stanzas = []

    for el in body:
        if el.tag in ("h2", "h3"):
            text = el.text_content().strip().replace("\n", " ")
            text = re.sub(r"\s+", " ", text)

            if text in POEMS1807V1_SKIP:
                continue
            if text in POEMS1807V1_SECTIONS:
                continue

            # Check for SONNETS section header
            if text == "SONNETS":
                continue

            # Notes at end
            if "NOTE" in text:
                in_notes = True
                continue

            # Map title
            title = POEMS1807V1_TITLE_MAP.get(text)
            if title is None and el.tag == "h3":
                title = POEMS1807V1_H3_TITLES.get(text)
            if title is None:
                # Generic title cleanup
                title = text.rstrip(".")
                # Strip leading number
                title = re.sub(r"^\d+\.\s*", "", title)
                # Title-case if ALL CAPS
                if title == title.upper() and len(title) > 3:
                    title = _title_case(title)

            _flush()
            current_title = title
            current_stanzas = []
            continue

        if el.tag == "h4":
            text = el.text_content().strip()
            if "NOTE" in text or text == "END OF THE FIRST VOLUME.":
                in_notes = True
            # h4 SONNETS is a section marker
            continue

        if in_notes:
            continue

        if el.tag != "p":
            continue

        if _is_verse_p(el):
            lines = _extract_verse_from_p(el)
            if lines and current_title:
                current_stanzas.append(lines)

    _flush()
    return poems


# ---------------------------------------------------------------------------
# Poems in Two Volumes 1807, Volume 2 (#8824)
# ---------------------------------------------------------------------------

# Fingerprints for poems in 1807v2 that lack proper headings.
# Maps first ~35 chars of first verse line → display title.
POEMS1807V2_FINGERPRINTS = {
    "A famous Man is Robin Hood": "Rob Roy's Grave",
    "Behold her, single in the field": "The Solitary Reaper",
    "Sweet Highland Girl, a very shower": "To the Highland Girl of Inversneyde",
    "Stay near me—do not take thy flight": "To a Butterfly [I]",
    "I wandered lonely as a Cloud": "Daffodils",
    "Who fancied what a pretty sight": "The Foresight",
    "Look, five blue eggs are gleaming there": "To a Butterfly [II]",
    "O blithe New-comer! I have heard": "To the Cuckoo",
    "Art thou a Statesman, in the van": "A Poet's Epitaph",
    # These three are caught by inline title detection, don't duplicate:
    # "High in the breathless Hall" → Song at the Feast of Brougham Castle
    # "Loud is the Vale!" → Lines Composed at Grasmere
    # "I was thy Neighbour once" → Elegiac Stanzas
    "There was a time when meadow, grove, and stream": "Ode: Intimations of Immortality",
    "My heart leaps up when I behold": "My Heart Leaps Up",
    "Spade! with which Wilkinson hath till'd his Lands": "To the Spade of a Friend",
    "The cock is crowing": "Written in March",
    "There is a Flower, the Lesser Celandine": "The Small Celandine",
    "The Sun has long been set": "The Sun Has Long Been Set",
    "O Nightingale! thou surely art": "O Nightingale!",
    "I\u2019ve watch\u2019d you now a full half hour": "To a Butterfly [II]",
    "I've watch'd you now a full half hour": "To a Butterfly [II]",
    "It is no Spirit who from Heaven hath flown": "Sonnet [It Is No Spirit]",
    "Now we are tired of boisterous joy": "The Blind Highland Boy",
    # "Dear Child of Nature" → To a Young Lady (caught by inline title)
    "By their floating Mill": "Stray Pleasures",
    "What crowd is this? what have we here": "Star-Gazers",
    "An Orpheus! An Orpheus": "Power of Music",
    "The May is come again": "The Green Linnet",
    "Yes, it was the mountain Echo": "Yes, It Was the Mountain Echo",
    "Degenerate Douglas! oh, the unworthy Lord": "Sonnet [Degenerate Douglas]",
    "Once in a lonely Hamlet I sojourn'd": "The Emigrant Mother",
    "That is work which I am rueing": "Foresight",
    "There is a change—and I am poor": "A Complaint",
    "There is a change\u2014and I am poor": "A Complaint",
    "I am not One who much or oft delight": "Personal Talk [I]",
    "Wings have we, and as far as we can go": "Personal Talk [III]",
    "Yes! full surely 'twas the Echo": "Yes, It Was the Mountain Echo",
}

# Inline title patterns in 1807v2 first lines (ALL CAPS titles embedded in verse paras)
POEMS1807V2_INLINE_TITLES = [
    (r"^(?:\d+\.\s*)?GLEN-ALMAIN", "Glen-Almain"),
    (r"^(?:\d+\.\s*)?SONNET\.\s*\(Composed at", "Sonnet [Composed at Castle]"),
    (r"^(?:\d+\.\s*)?ADDRESS TO THE SONS OF BURNS", "Address to the Sons of Burns"),
    (r"^(?:\d+\.\s*)?YARROW UNVISITED", "Yarrow Unvisited"),
    (r"^(?:\d+\.\s*)?WRITTEN IN MARCH", "Written in March"),
    (r"^(?:\d+\.\s*)?THE SMALL CELANDINE", "The Small Celandine"),
    (r"^THE BLIND HIGHLAND BOY", "The Blind Highland Boy"),
    (r"^TO A YOUNG LADY", "To a Young Lady"),
    (r"^SONG,?\s*AT THE FEAST OF BROUGHAM", "Song at the Feast of Brougham Castle"),
    (r"^LINES,?\s*Composed at GRASMERE", "Lines Composed at Grasmere"),
    (r"^ELEGIAC STANZAS", "Elegiac Stanzas"),
]

# Title paragraphs in 1807v2
POEMS1807V2_TITLE_PARAS = {
    "ROB ROY's GRAVE": None,  # Detected by fingerprint
    "(At Inversneyde, upon Loch Lomond.)": None,  # Subtitle, not a new poem
    "Paulo majora canamus": None,  # Epigraph before Immortality Ode
}

# Headings to skip
POEMS1807V2_SKIP = {
    "The Project Gutenberg eBook of Poems in Two Volumes, Volume 2",
    "END OF THE SECOND VOLUME.",
    "THE FULL PROJECT GUTENBERG LICENSE",
}


def parse_poems1807v2() -> list[dict]:
    """Parse Poems in Two Volumes 1807, Volume 2."""
    path = RAW_DIR / "wordsworth_poems1807v2.html"
    doc = lhtml.fromstring(path.read_bytes())
    body = doc.find(".//body")
    if body is None:
        return []

    poems = []
    current_title = None
    current_stanzas = []
    in_notes = False

    def _flush():
        nonlocal current_title, current_stanzas
        if current_title and current_stanzas:
            poems.append(stanzas_to_poem_dict(current_title, current_stanzas))
        current_title = None
        current_stanzas = []

    for el in body:
        if el.tag in ("h2",):
            text = el.text_content().strip().replace("\n", " ")
            text = re.sub(r"\s+", " ", text)

            if text in POEMS1807V2_SKIP:
                continue

            # Poems with h2 headings: "3. STEPPING WESTWARD.", "5. THE MATRON...",
            # "1. TO A BUTTERFLY.", "10. GIPSIES."
            _flush()
            title = text.rstrip(".")
            title = re.sub(r"^\d+\.\s*", "", title)
            if title == title.upper() and len(title) > 3:
                title = _title_case(title)
            current_title = title
            current_stanzas = []
            continue

        if el.tag == "h4":
            text = el.text_content().strip()
            if "NOTE" in text.upper():
                in_notes = True
                continue
            # "POEMS WRITTEN DURING A TOUR IN SCOTLAND." — section header
            if "ODE." == text:
                # This is the Immortality Ode heading, but it will be caught by fingerprint
                continue
            continue

        if in_notes:
            continue

        if el.tag != "p":
            continue

        text = el.text_content().strip()
        if not text:
            continue

        # Skip footnotes
        if text.startswith("[Footnote"):
            continue

        # Notes section marker
        if text.startswith("NOTES to the SECOND"):
            in_notes = True
            continue

        if _is_verse_p(el):
            lines = _extract_verse_from_p(el)
            if not lines:
                continue

            first_line = lines[0].strip()

            # Check for inline titles in the first line of a verse paragraph
            # e.g., "SONG, AT THE FEAST OF BROUGHAM CASTLE, ..."
            # e.g., "LINES, Composed at GRASMERE..."
            # e.g., "ELEGIAC STANZAS, Suggested by a Picture..."
            # e.g., "5. WRITTEN IN MARCH, While resting on the Bridge..."
            # e.g., "6. THE SMALL CELANDINE. Common Pilewort."
            # e.g., "4. GLEN-ALMAIN, or the NARROW GLEN"
            # e.g., "7. SONNET. (Composed at —— Castle.)"
            # e.g., "8. ADDRESS TO THE SONS OF BURNS ..."
            # e.g., "9. YARROW UNVISITED. ..."
            inline_title = _check_inline_title_1807v2(first_line, lines)
            if inline_title:
                _flush()
                current_title = inline_title
                # Strip the title line(s)
                lines = _strip_title_lines(lines)
                if lines:
                    current_stanzas.append(lines)
                continue

            # Check fingerprints
            matched = False
            for fingerprint, title in POEMS1807V2_FINGERPRINTS.items():
                if first_line.startswith(fingerprint[:35]):
                    _flush()
                    current_title = title
                    current_stanzas.append(lines)
                    matched = True
                    break

            if not matched and current_title:
                current_stanzas.append(lines)

        elif _is_title_p(el):
            # Asterisk separators (* * * * *) mark poem boundaries
            if re.match(r"^\*[\s*]+\*$", text.strip()):
                _flush()
                continue

            # Title paragraphs like "ROB ROY's GRAVE."
            if text.startswith("ROB ROY"):
                pass  # caught by fingerprint on next verse para
            elif text.strip().startswith("(At Inversneyde"):
                pass  # subtitle
            elif text == "Paulo majora canamus.":
                pass  # epigraph before Immortality Ode

            # Numbered bare titles: "2.", "3.", "4.", "7.", "8."
            num_match = re.match(r"^(\d+)\.\s*$", text.strip())
            if num_match:
                pass  # caught by fingerprint on next verse paragraph

    _flush()

    # Post-process: merge consecutive poems with same or similar title
    merged = []
    for p in poems:
        if merged:
            prev = merged[-1]["title"]
            curr = p["title"]
            # Merge if same title, or if one starts with the other
            # BUT don't merge generic titles like "Sonnet" — they're different poems
            is_generic = prev.lower() in ("sonnet", "lines", "ode")
            if not is_generic and (prev == curr or prev.startswith(curr) or curr.startswith(prev)):
                # Keep the shorter (cleaner) title
                if len(curr) < len(prev):
                    merged[-1]["title"] = curr
                merged[-1]["stanzas"].extend(p["stanzas"])
                continue
        merged.append(p)

    # Remove poems with 0 or very few lines (stray fragments)
    merged = [p for p in merged
              if sum(len(s["lines"]) for s in p["stanzas"]) > 1]

    # Title fixups
    for p in merged:
        if p["title"] == "Lines":
            p["title"] = "Lines Composed at Grasmere"
        if p["title"] == "Incident":
            p["title"] = "Incident Characteristic of a Favourite Dog"

    return merged


def _check_inline_title_1807v2(first_line: str, lines: list[str]) -> str | None:
    """Check if the first line of a verse paragraph is an inline title.

    Returns the title if found, None otherwise.
    """
    # Check explicit patterns first
    for pat, title in POEMS1807V2_INLINE_TITLES:
        if re.match(pat, first_line):
            return title

    # Generic: if the first line is ALL CAPS (possibly with punctuation/numbers)
    # and short, treat it as an inline title
    stripped = re.sub(r"^\d+\.\s*", "", first_line).strip()
    # Remove trailing punctuation for the check
    alpha = re.sub(r"[^a-zA-Z]", "", stripped)
    if alpha and alpha == alpha.upper() and len(stripped) < 80 and len(alpha) > 3:
        # This is an ALL CAPS title line
        title = stripped.rstrip(".,;:")
        # Extract just the main title (before multi-space gap or long subtitle)
        # e.g., "SONNET,     TO THOMAS CLARKSON,       On the final..." → "Sonnet, to Thomas Clarkson"
        # e.g., "TO THE SPADE OF A FRIEND, (AN AGRICULTURIST.)       Composed while..." → "To the Spade of a Friend"
        parts = re.split(r"\s{4,}", title)
        title = parts[0].rstrip(".,;:()")
        return _title_case(title)
    return None


def _strip_title_lines(lines: list[str]) -> list[str]:
    """Strip title/subtitle lines from the start of a verse block.

    Handles patterns like:
      - "6. THE SMALL CELANDINE." / "Common Pilewort."
      - "SONG, AT THE FEAST OF BROUGHAM CASTLE," / "Upon the RESTORATION..."
      - "THE BLIND HIGHLAND BOY." / "(A Tale told by the Fire-side.)"
    """
    if not lines:
        return lines
    # Strip lines until we hit one that looks like verse (not ALL CAPS, not a subtitle)
    result = list(lines)
    while result:
        line = result[0].strip()
        # Title line: ALL CAPS, numbered prefix, or very short
        is_title_like = (
            (re.match(r"^\d+\.\s*[A-Z]", line) and len(line) < 80)
            or (line == line.upper() and len(line) > 2 and len(line) < 80)
            or re.match(r"^\(A Tale|^\(At |^Common |^or the |^Upon the |^after visiting|^While resting|^Who had been|^upon", line, re.IGNORECASE)
        )
        if is_title_like:
            result.pop(0)
        else:
            break
    return result


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _title_case(text: str) -> str:
    """Smart title-casing for ALL CAPS titles."""
    words = text.split()
    small = {"of", "the", "a", "an", "and", "in", "on", "to", "at", "for", "or", "upon",
             "near", "from", "with", "by", "but", "as", "yet", "so"}
    cased = []
    for i, w in enumerate(words):
        if i > 0 and w.lower() in small:
            cased.append(w.lower())
        else:
            cased.append(w.capitalize())
    return " ".join(cased)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_intermediate(label: str, gut_id: str, date: str, poems: list[dict]):
    """Write one intermediate JSON file."""
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40]
    out = {
        "tcp_id": "",
        "gutenberg_id": gut_id,
        "author": POET,
        "title": label,
        "date": date,
        "source": "Gutenberg",
        "poems": poems,
    }
    path = INTERMEDIATE_DIR / f"GUT_wordsworth-{slug}.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    total_lines = sum(
        sum(len(s["lines"]) for s in p["stanzas"])
        for p in poems
    )
    print(f"  {path.name}: {len(poems)} poems, {total_lines} lines")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    use_cached = "--cached" in sys.argv

    if not use_cached:
        print("Downloading from Gutenberg...")
        ensure_downloaded()
    else:
        print("Using cached HTML files")

    print("\n=== Lyrical Ballads 1798 (#9622) ===")
    lb1798 = parse_lb1798()
    for p in lb1798:
        n = sum(len(s["lines"]) for s in p["stanzas"])
        print(f"  {p['title']}: {len(p['stanzas'])} st, {n} ln")

    print(f"\n=== Lyrical Ballads 1800, Vol 2 (#8912) ===")
    lb1800v2 = parse_lb1800v2()
    for p in lb1800v2:
        n = sum(len(s["lines"]) for s in p["stanzas"])
        print(f"  {p['title']}: {len(p['stanzas'])} st, {n} ln")

    print(f"\n=== Poems in Two Volumes 1807, Vol 1 (#8774) ===")
    p1807v1 = parse_poems1807v1()
    for p in p1807v1:
        n = sum(len(s["lines"]) for s in p["stanzas"])
        print(f"  {p['title']}: {len(p['stanzas'])} st, {n} ln")

    print(f"\n=== Poems in Two Volumes 1807, Vol 2 (#8824) ===")
    p1807v2 = parse_poems1807v2()
    for p in p1807v2:
        n = sum(len(s["lines"]) for s in p["stanzas"])
        print(f"  {p['title']}: {len(p['stanzas'])} st, {n} ln")

    # Dedup across volumes (1798 vs 1800, etc.)
    # For now, keep all — dedup happens at chunk/ingest level
    print(f"\nWriting intermediate JSON...")
    write_intermediate("Lyrical Ballads 1798", "9622", "1798", lb1798)
    write_intermediate("Lyrical Ballads 1800 Vol 2", "8912", "1800", lb1800v2)
    write_intermediate("Poems in Two Volumes 1807 Vol 1", "8774", "1807", p1807v1)
    write_intermediate("Poems in Two Volumes 1807 Vol 2", "8824", "1807", p1807v2)

    total = len(lb1798) + len(lb1800v2) + len(p1807v1) + len(p1807v2)
    print(f"\nDone. {total} poems total across 4 volumes.")
    print("Run chunk_corpus.py and ingest_to_chroma.py to embed.")


if __name__ == "__main__":
    main()
