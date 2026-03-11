#!/usr/bin/env python3
"""
parse_eebo_drama.py — Extract verse speeches from EEBO-TCP play XMLs.

General-purpose parser for dramatic verse: Shakespeare Folio, Jacobean drama,
Restoration plays, Jonson's Workes. Extracts speeches of N+ verse lines,
tags with speaker and play title.

Outputs intermediate JSON compatible with chunk_corpus.py.

Usage:
    python scripts/parse_eebo_drama.py              # process all targets
    python scripts/parse_eebo_drama.py --download    # download missing XMLs first
    python scripts/parse_eebo_drama.py --list        # show target list
"""

import argparse
import json
import re
import urllib.request
from collections import defaultdict
from pathlib import Path

from lxml import etree

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RAW_DIR = Path("corpus/raw")
OUT_DIR = Path("corpus/intermediate")

NS = {"tei": "http://www.tei-c.org/ns/1.0"}

MIN_VERSE_LINES = 4  # minimum lines for a speech to be included

XML_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/textcreationpartnership/{tcp_id}/master/{tcp_id}.xml"
)

# ---------------------------------------------------------------------------
# Target plays: (tcp_id, poet, play_title_override, note)
#
# play_title_override: if set, used instead of XML head (for multi-play volumes)
# For the Folio (A11954) and Jonson Workes (A04632), plays are extracted
# individually — entries below mark which plays to keep.
# ---------------------------------------------------------------------------

# Shakespeare Folio — all plays extracted automatically
FOLIO_TCP = "A11954"
FOLIO_POET = "William Shakespeare"

# Jonson Workes — specific plays only
JONSON_TCP = "A04632"
JONSON_POET = "Ben Jonson"
JONSON_PLAYS = {"Sejanus", "Volpone", "The Alchemist"}

# Individual play files
SINGLE_PLAYS = [
    # Marlowe
    ("A07009", "Christopher Marlowe", "Doctor Faustus"),
    ("A07018", "Christopher Marlowe", "Edward the Second"),
    ("A07004", "Christopher Marlowe", "Tamburlaine the Great"),
    ("A07023", "Christopher Marlowe", "Dido, Queen of Carthage"),
    # Webster
    ("A14872", "John Webster", "The Duchess of Malfi"),
    ("A14875", "John Webster", "The White Devil"),
    # Ford
    ("A01057", "John Ford", "'Tis Pity She's a Whore"),
    # Tourneur
    ("A13843", "Cyril Tourneur", "The Revenger's Tragedy"),
    ("A13840", "Cyril Tourneur", "The Atheist's Tragedy"),
    # Kyd
    ("A04942", "Thomas Kyd", "The Spanish Tragedy"),
    # Middleton
    ("A50789", "Thomas Middleton", "The Changeling"),
    ("A50799", "Thomas Middleton", "WOMEN BEWARE WOMEN"),  # Two New Playes — only want WBW
    ("A07493", "Thomas Middleton", "A Chaste Maid in Cheapside"),
    ("A07524", "Thomas Middleton", "The Roaring Girl"),
    ("A07498", "Thomas Middleton", "A Game at Chess"),
    # Otway
    ("A53535", "Thomas Otway", "Venice Preserv'd"),
    ("A53521", "Thomas Otway", "The Orphan"),
]


def all_tcp_ids() -> list[str]:
    """Return all TCP IDs we need."""
    ids = [FOLIO_TCP, JONSON_TCP]
    ids.extend(tcp for tcp, _, _ in SINGLE_PLAYS)
    return ids


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _text(el) -> str:
    """Extract all text from an element, stripping whitespace."""
    return "".join(el.itertext()).strip()


def _clean_title(raw: str) -> str:
    """Clean up an EEBO title: normalise whitespace, strip trailing periods."""
    title = re.sub(r"\s+", " ", raw).strip().rstrip(".")
    return title


def _extract_speeches(container, min_lines: int = MIN_VERSE_LINES) -> list[dict]:
    """Extract verse speeches from a container element (play div, act div, etc.).

    Returns list of {"speaker": str, "lines": [str], "line_count": int}.
    """
    speeches = []
    for sp in container.findall(f".//{{{NS['tei']}}}sp"):
        # Check direct children first, then descendants (some texts nest <l> in <lg>)
        verse_lines = sp.findall(f"{{{NS['tei']}}}l")
        if not verse_lines:
            verse_lines = sp.findall(f".//{{{NS['tei']}}}l")
        if len(verse_lines) < min_lines:
            continue

        speaker_el = sp.find(f"{{{NS['tei']}}}speaker")
        speaker = _text(speaker_el) if speaker_el is not None else ""
        # Clean speaker: remove trailing periods, normalise
        speaker = speaker.strip().rstrip(".")

        lines = [_text(l) for l in verse_lines]
        # Skip if lines are mostly empty (parsing artifacts)
        non_empty = [l for l in lines if l]
        if len(non_empty) < min_lines:
            continue

        speeches.append({
            "speaker": speaker,
            "lines": non_empty,
            "line_count": len(non_empty),
        })

    return speeches


def _speeches_to_poems(speeches: list[dict], play_title: str) -> list[dict]:
    """Convert extracted speeches to intermediate JSON poem format.

    Each speech becomes a "poem" with a single stanza, speaker field,
    and title like "Hamlet, Act III — Hamlet".
    """
    poems = []
    for sp in speeches:
        poem = {
            "title": play_title,
            "speaker": sp["speaker"],
            "div_type": "speech",
            "stanzas": [{
                "stanza_num": 1,
                "lines": sp["lines"],
                "gaps": [],
            }],
        }
        poems.append(poem)
    return poems


# ---------------------------------------------------------------------------
# Folio parser (Shakespeare)
# ---------------------------------------------------------------------------

def parse_folio(xml_path: Path) -> list[dict]:
    """Parse the First Folio. Returns list of intermediate JSON dicts (one per play)."""
    tree = etree.parse(str(xml_path))
    root = tree.getroot()

    # Troilus and Cressida has no head in the Folio — identify by position/content
    HEADLESS_PLAYS = {
        # Detected by checking for known character names in speaker tags
        "Troylus": "THE TRAGEDIE OF Troylus and Cressida",
    }

    results = []
    plays = root.findall(f".//{{{NS['tei']}}}div[@type='play']")

    for play_div in plays:
        head = play_div.find(f"{{{NS['tei']}}}head")
        if head is not None:
            raw_title = _text(head)
        else:
            # Try to identify headless play by speaker names
            speakers = {_text(s) for s in play_div.findall(f".//{{{NS['tei']}}}speaker")}
            raw_title = "Unknown Play"
            for marker, title in HEADLESS_PLAYS.items():
                if any(marker.upper() in s.upper() for s in speakers):
                    raw_title = title
                    break
        play_title = _clean_title(raw_title)

        speeches = _extract_speeches(play_div)
        if not speeches:
            continue

        poems = _speeches_to_poems(speeches, play_title)

        results.append({
            "tcp_id": FOLIO_TCP,
            "author": FOLIO_POET,
            "title": play_title,
            "date": "1623",
            "source": "EEBO-TCP",
            "poems": poems,
            "gap_log": [],
        })

    return results


# ---------------------------------------------------------------------------
# Jonson Workes parser
# ---------------------------------------------------------------------------

def _identify_jonson_play(text_el) -> str | None:
    """Identify which play a <text> element in the Workes belongs to.

    Returns normalised play name or None if not a target play.
    Only matches act-level headings to avoid false positives from
    commendatory poems and masques.
    """
    # Look for act headings that name the play, or argument/prologue headings
    for head in text_el.findall(f".//{{{NS['tei']}}}head"):
        ht = _text(head).upper()
        # Skip the top-level catalogue/collection
        if "CATALOGUE" in ht or "WORKES" in ht:
            return None
        # Match specific plays — require "Act" nearby or "ARGVMENT"
        if ("SEIANVS" in ht or "SEJANUS" in ht) and ("ACT" in ht or "ARGVMENT" in ht):
            return "Sejanus"
        if ("VOLPONE" in ht or "THE FOXE" in ht) and ("ARGVMENT" in ht or "PROLOGVE" in ht):
            return "Volpone"
        if "ALCHEMIST" in ht and "THE ARGVMENT" in ht:
            return "The Alchemist"
    return None


def parse_jonson_workes(xml_path: Path) -> list[dict]:
    """Parse Jonson's 1616 Workes. Returns intermediate JSONs for target plays only."""
    tree = etree.parse(str(xml_path))
    root = tree.getroot()

    results = []
    for text_el in root.findall(f".//{{{NS['tei']}}}text"):
        # Skip the top-level wrapper that contains all plays
        all_sp = text_el.findall(f".//{{{NS['tei']}}}sp")
        if len(all_sp) > 3000:
            continue

        play_name = _identify_jonson_play(text_el)
        if play_name not in JONSON_PLAYS:
            continue

        speeches = _extract_speeches(text_el)
        if not speeches:
            continue

        poems = _speeches_to_poems(speeches, play_name)

        results.append({
            "tcp_id": JONSON_TCP,
            "author": JONSON_POET,
            "title": play_name,
            "date": "1616",
            "source": "EEBO-TCP",
            "poems": poems,
            "gap_log": [],
        })

    return results


# ---------------------------------------------------------------------------
# Single-play parser (Webster, Ford, Tourneur, Kyd, Middleton, Otway, Marlowe)
# ---------------------------------------------------------------------------

def parse_single_play(xml_path: Path, tcp_id: str, poet: str,
                      title_override: str | None = None) -> list[dict]:
    """Parse a single-play TCP XML. Returns list of intermediate JSON dicts.

    For multi-play volumes (e.g. A50799 — Two New Playes), returns one dict
    per play found in the file.
    """
    tree = etree.parse(str(xml_path))
    root = tree.getroot()

    # Try to get date from sourceDesc
    date = ""
    for date_el in root.findall(f".//{{{NS['tei']}}}sourceDesc//{{{NS['tei']}}}date"):
        date = date_el.text or date_el.get("when", "")
        if date:
            break

    # Check for multiple text elements (multi-play volumes)
    texts = root.findall(f".//{{{NS['tei']}}}text")

    results = []

    # Headings to skip when looking for play titles (frontispieces, commendatory, etc.)
    _SKIP_HEADS = {"Vera Effigies", "TO THE READER", "UPON", "The Actors", "Actors Names",
                   "Dramatis", "PROLOGUE", "EPILOGUE"}

    if len(texts) > 1:
        # Multi-play volume — extract each play separately
        for text_el in texts:
            # Skip the top-level wrapper
            all_sp = text_el.findall(f".//{{{NS['tei']}}}sp")
            if len(all_sp) > 2000:
                continue

            # Find play title from headings, skipping frontispiece etc.
            play_title = None
            for head in text_el.findall(f".//{{{NS['tei']}}}head"):
                ht = _text(head)
                if len(ht) < 5 or "Act" in ht or "Scene" in ht or "Scaen" in ht:
                    continue
                if any(ht.startswith(skip) or skip in ht for skip in _SKIP_HEADS):
                    continue
                play_title = _clean_title(ht)
                break

            if not play_title:
                continue  # can't identify this play

            # If title_override is set, only extract that specific play
            if title_override and title_override.upper() not in play_title.upper():
                continue

            speeches = _extract_speeches(text_el)
            if not speeches:
                continue

            poems = _speeches_to_poems(speeches, play_title)

            results.append({
                "tcp_id": tcp_id,
                "author": poet,
                "title": play_title,
                "date": date,
                "source": "EEBO-TCP",
                "poems": poems,
                "gap_log": [],
            })
    else:
        # Single play
        if title_override:
            play_title = title_override
        else:
            # Try to extract from title page
            for head in root.findall(f".//{{{NS['tei']}}}head"):
                ht = _text(head)
                if len(ht) > 10 and "Act" not in ht:
                    play_title = _clean_title(ht)
                    break
            else:
                # Fall back to titleStmt
                title_el = root.find(f".//{{{NS['tei']}}}titleStmt/{{{NS['tei']}}}title")
                play_title = _clean_title(_text(title_el)) if title_el is not None else tcp_id

        speeches = _extract_speeches(root)
        if speeches:
            poems = _speeches_to_poems(speeches, play_title)
            results.append({
                "tcp_id": tcp_id,
                "author": poet,
                "title": play_title,
                "date": date,
                "source": "EEBO-TCP",
                "poems": poems,
                "gap_log": [],
            })

    return results


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_missing():
    """Download any missing TCP XMLs."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for tcp_id in all_tcp_ids():
        dest = RAW_DIR / f"{tcp_id}.xml"
        if dest.exists():
            print(f"  Already have {tcp_id}")
            continue
        url = XML_URL_TEMPLATE.format(tcp_id=tcp_id)
        print(f"  Downloading {tcp_id} ...")
        try:
            urllib.request.urlretrieve(url, str(dest))
            print(f"    → {dest}")
        except Exception as e:
            print(f"    ERROR: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Extract verse speeches from EEBO-TCP plays")
    parser.add_argument("--download", action="store_true", help="Download missing XMLs first")
    parser.add_argument("--list", action="store_true", help="List target plays and exit")
    args = parser.parse_args()

    if args.list:
        print("=== Shakespeare Folio (A11954) — all 36 plays ===")
        print(f"\n=== Jonson Workes (A04632) — {', '.join(sorted(JONSON_PLAYS))} ===")
        print("\n=== Individual plays ===")
        for tcp, poet, title in SINGLE_PLAYS:
            print(f"  {tcp}: {poet} — {title or '(from XML)'}")
        return

    if args.download:
        print("Downloading missing XMLs ...")
        download_missing()
        print()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total_speeches = 0
    total_plays = 0

    # --- Shakespeare Folio ---
    folio_path = RAW_DIR / f"{FOLIO_TCP}.xml"
    if folio_path.exists():
        print(f"Parsing Shakespeare Folio ({FOLIO_TCP}) ...")
        play_dicts = parse_folio(folio_path)
        for d in play_dicts:
            n = len(d["poems"])
            total_speeches += n
            total_plays += 1
            out_file = OUT_DIR / f"DRAMA_{FOLIO_TCP}_{_slugify(d['title'])}.json"
            with open(out_file, "w") as f:
                json.dump(d, f, indent=2, ensure_ascii=False)
            print(f"  {d['title']}: {n} speeches → {out_file.name}")
    else:
        print(f"WARNING: Folio not found at {folio_path}. Run with --download.")

    # --- Jonson Workes ---
    jonson_path = RAW_DIR / f"{JONSON_TCP}.xml"
    if jonson_path.exists():
        print(f"\nParsing Jonson Workes ({JONSON_TCP}) ...")
        play_dicts = parse_jonson_workes(jonson_path)
        for d in play_dicts:
            n = len(d["poems"])
            total_speeches += n
            total_plays += 1
            out_file = OUT_DIR / f"DRAMA_{JONSON_TCP}_{_slugify(d['title'])}.json"
            with open(out_file, "w") as f:
                json.dump(d, f, indent=2, ensure_ascii=False)
            print(f"  {d['title']}: {n} speeches → {out_file.name}")
    else:
        print(f"WARNING: Jonson Workes not found at {jonson_path}. Run with --download.")

    # --- Single plays ---
    print(f"\nParsing individual plays ...")
    for tcp_id, poet, title_override in SINGLE_PLAYS:
        xml_path = RAW_DIR / f"{tcp_id}.xml"
        if not xml_path.exists():
            print(f"  SKIP {tcp_id} ({poet}): not downloaded. Run with --download.")
            continue

        play_dicts = parse_single_play(xml_path, tcp_id, poet, title_override)
        for d in play_dicts:
            n = len(d["poems"])
            total_speeches += n
            total_plays += 1
            out_file = OUT_DIR / f"DRAMA_{tcp_id}_{_slugify(d['title'])}.json"
            with open(out_file, "w") as f:
                json.dump(d, f, indent=2, ensure_ascii=False)
            print(f"  {poet} — {d['title']}: {n} speeches → {out_file.name}")

    print(f"\nDone. {total_plays} plays, {total_speeches} verse speeches extracted.")


def _slugify(text: str) -> str:
    """Make a filename-safe slug from a title."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s-]+", "-", slug).strip("-")
    return slug[:60]


if __name__ == "__main__":
    main()
