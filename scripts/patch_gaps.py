#!/usr/bin/env python3
"""
patch_gaps.py — Cross-reference gaps in EEBO-TCP texts against clean editions.

For texts with <gap> elements (notably Milton's Paradise Lost), this script:
1. Loads the intermediate JSON and its gap log
2. Fetches a clean reference text (Standard Ebooks or Project Gutenberg)
3. Uses fuzzy string matching to align context and extract missing words
4. Patches the intermediate JSON
5. Logs all patches for human review

Usage:
    python scripts/patch_gaps.py                          # Patch all texts with gaps
    python scripts/patch_gaps.py corpus/intermediate/A50919.json  # Patch a single file
"""

import json
import os
import re
import sys
from pathlib import Path

import requests
import yaml
from thefuzz import fuzz, process


def load_config(config_path: str = "config/config.yaml") -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Pass 1: Local inference for trivial gaps (1-2 letter fills, obvious words)
# ---------------------------------------------------------------------------

def infer_local_patch(context: str, extent: str) -> str | None:
    """
    Try to fill a gap locally without a clean source.
    Works for:
      - 1 letter gaps where the surrounding characters make it unambiguous
        e.g. "ta[...]k" → "task", "heave[...]" → "heaven"
      - 2 letter gaps at word endings e.g. "te[...]" → "tears"
      - 1 word gaps at line starts/ends with strong syntactic cues
    Returns the fill string, or None if not confident.
    """
    if "[...]" not in context:
        return None

    # Split around the gap
    before, after = context.split("[...]", 1)

    # --- 1-letter and 2-letter fills within a word ---
    if extent in ("1 letter", "1 char", "1 letters"):
        return _infer_letter_gap(before, after, 1)
    if extent in ("2 letters", "2 chars"):
        return _infer_letter_gap(before, after, 2)

    return None


def _infer_letter_gap(before: str, after: str, n_chars: int) -> str | None:
    """
    Infer missing letter(s) within a word.
    'before' ends with the partial word prefix, 'after' starts with the suffix.
    """
    # Extract the partial word fragments around the gap
    # e.g. before="A broken ALTAR, Lord, thy servant rea" after="s,"
    #   prefix_frag = "rea", suffix_frag = "s"
    # e.g. before="Th" after="y may weep"
    #   prefix_frag = "Th", suffix_frag = "y"
    # e.g. before="O let thy blessed SACRIFICE be mi" after=""  (line ends)
    #   prefix_frag = "mi", suffix_frag = ""

    # Get the word fragment before the gap (chars after last space)
    prefix_frag = ""
    before_stripped = before.rstrip()
    if before_stripped:
        last_word = before_stripped.split()[-1]
        # The prefix is the alphabetic tail of what's before the gap
        m = re.search(r"([A-Za-z]+)$", before_stripped)
        prefix_frag = m.group(1) if m else ""
    # Handle case where gap is at start of line: before is empty or whitespace
    if not before or before[-1] == " ":
        prefix_frag = ""

    # Get the word fragment after the gap (alphabetic chars before space/punctuation)
    suffix_frag = ""
    if after:
        m = re.match(r"([A-Za-z]+)", after)
        if m:
            suffix_frag = m.group(1)

    # Now we need: prefix_frag + ??? (n_chars) + suffix_frag = a real word
    # Try common English letter patterns
    candidates = _generate_candidates(prefix_frag, suffix_frag, n_chars)

    if len(candidates) == 1:
        return candidates[0]

    # If multiple candidates, don't guess
    return None


# A list of common English words for validation.
# We only need enough to cover the kinds of words that appear in early modern poetry.
# This is loaded lazily.
_WORD_SET = None


def _get_word_set() -> set[str]:
    """Build a set of common English words for candidate validation."""
    global _WORD_SET
    if _WORD_SET is not None:
        return _WORD_SET

    # Common words for validating 1-2 letter gap fills in early modern poetry.
    # We load from /usr/share/dict/words if available, otherwise use a built-in set.
    common = set()

    # Try system dictionary first — much more comprehensive
    dict_path = "/usr/share/dict/words"
    if os.path.exists(dict_path):
        with open(dict_path) as f:
            for line in f:
                w = line.strip()
                if 2 <= len(w) <= 20:
                    common.add(w.lower())
        # Generate common inflections the system dict is missing
        base_words = list(common)
        for w in base_words:
            if len(w) < 2:
                continue
            # Plurals and verb forms
            common.add(w + "s")
            common.add(w + "es")
            common.add(w + "ed")
            common.add(w + "er")
            common.add(w + "est")
            common.add(w + "ing")
            common.add(w + "ly")
            common.add(w + "th")     # early modern: doth, hath, runneth
            common.add(w + "st")     # early modern: dost, hast
            # Handle -e endings: love → loved, loves, loving, lover
            if w.endswith("e"):
                common.add(w + "d")
                common.add(w + "r")
                common.add(w[:-1] + "ing")
                common.add(w[:-1] + "ed")

    # Always add early modern / archaic forms not in system dict
    common.update({
        "thou", "thee", "thine", "thy", "hath", "doth", "dost", "hast",
        "wilt", "shalt", "canst", "didst", "wouldst", "couldst", "shouldst",
        "whence", "thence", "hence", "wherein", "thereof", "wherefore",
        "yea", "nay", "ere", "oft", "nought", "aught",
        "ne", "ye", "ay", "io",
        # Early modern spellings common in EEBO
        "sinne", "sunne", "bloud", "onely", "heav'n", "prayse",
        "musick", "physick", "sonne", "farre", "shew", "warre",
        "hee", "shee", "wee", "bee", "mee", "thee",
        "beautie", "dutie", "pitie", "bountie", "gentrie",
        "prentice", "balsome", "confound", "rears",
        "crosse", "losse", "glasse", "grasse",
        "shal", "wil", "stil", "ful", "al",
        "vertue", "vpon", "vnto", "vnder",
    })
    _WORD_SET = {w.lower() for w in common}
    return _WORD_SET


def _generate_candidates(prefix: str, suffix: str, n: int) -> list[str]:
    """
    Generate plausible fill characters for a gap of n letters.
    Returns list of fill strings (just the missing chars, not the full word).
    If multiple candidates exist but one is clearly dominant, returns just that one.
    """
    words = _get_word_set()
    target_len = len(prefix) + n + len(suffix)
    candidate_fills = set()
    candidate_words = {}  # fill -> full word

    prefix_lower = prefix.lower()
    suffix_lower = suffix.lower()

    for word in words:
        if len(word) != target_len:
            continue
        if not word.startswith(prefix_lower):
            continue
        if suffix_lower and not word.endswith(suffix_lower):
            continue
        fill = word[len(prefix):len(word) - len(suffix)] if suffix else word[len(prefix):]
        if len(fill) == n:
            candidate_fills.add(fill)
            candidate_words[fill] = word

    candidates = list(candidate_fills)

    if len(candidates) <= 1:
        return candidates

    # If we have both prefix and suffix (gap is mid-word), the word is
    # strongly constrained — accept if unique or nearly so
    if prefix and suffix:
        if len(candidates) <= 3:
            # Few candidates with both sides constrained — rank and take best
            ranked = _rank_by_frequency(candidates, candidate_words)
            return [ranked[0]]
        # More candidates but still mid-word — only accept if one is in freq tier
        ranked = _rank_by_frequency(candidates, candidate_words)
        top_word = candidate_words.get(ranked[0], "")
        if top_word in _FREQ_TIERS:
            return [ranked[0]]
        return []

    # Gap at word boundary (start or end) — only accept if exactly 1 candidate
    # These are too ambiguous without context
    return []


# Rough frequency tiers — words more likely in poetry get priority.
# This is not a real frequency list, just enough to disambiguate common cases.
_FREQ_TIERS = {
    # Tier 0 (most common)
    "the", "and", "that", "have", "for", "not", "with", "you", "this",
    "but", "his", "from", "they", "been", "one", "had", "all", "she",
    "there", "when", "will", "each", "make", "can", "more", "her", "was",
    "what", "their", "said", "which", "than", "who", "may", "been",
    "would", "them", "shall", "some", "into", "over", "such",
    # Common in poetry
    "soul", "heart", "love", "death", "mine", "thine", "thou",
    "heaven", "earth", "light", "night", "sweet", "blood", "tears",
    "eyes", "hand", "hands", "face", "grace", "fear", "hope", "faith",
    "grief", "pain", "dust", "fire", "stone", "bone", "cross",
    "task", "rest", "lost", "find", "rise", "fall", "turn", "burn",
    "drops", "rears", "passions", "confound", "crown", "ground",
    "round", "sound", "bound", "found", "wound", "mound",
    # End-of-word completions common in poetry
    "heaven", "rears", "mine", "thine", "divine", "wine", "vine",
    "flowers", "powers", "towers", "hours",
    "espied", "denied", "replied", "supplied",
    "behold", "unfold", "withhold",
    "reproach", "reproaches",
    # Start-of-line words
    "let", "the", "and", "unto", "into", "love", "these",
    "those", "where", "there", "here", "before", "therefore",
    "remove", "affront", "fathom",
    # Herbert-specific
    "balsome", "sinne", "prentice", "gentrie",
    "sacrifice", "altar", "temple",
}


_BAD_BIGRAMS = {
    "ii", "uu", "qi", "qo", "qa", "qe", "qy", "bx", "cx", "dx",
    "fx", "gx", "hx", "jx", "kx", "lx", "mx", "nx", "px", "rx",
    "sx", "vx", "wx", "zx", "xj", "xk", "xq", "xz",
    "jj", "kk", "vv", "ww", "yy", "zz", "qq",
}


def _rank_by_frequency(fills: list[str], fill_to_word: dict) -> list[str]:
    """Rank candidate fills by word frequency/commonality."""
    def score(fill):
        word = fill_to_word.get(fill, "")
        if word in _FREQ_TIERS:
            return 0  # highest priority
        # Penalise words with unlikely bigrams
        penalty = 0
        for i in range(len(word) - 1):
            if word[i:i+2] in _BAD_BIGRAMS:
                penalty += 10
        # Prefer common English endings
        if word.endswith(("tion", "ness", "ment", "ous", "ing", "ght", "ble")):
            penalty -= 1
        return 1 + penalty

    ranked = sorted(fills, key=score)
    return ranked


# ---------------------------------------------------------------------------
# Pass 3: Gutenberg Poetry Corpus — universal line-level matching
# ---------------------------------------------------------------------------

GUTENBERG_POETRY_PATH = "corpus/raw/gutenberg-poetry-v001.ndjson"

_GUTENBERG_LINES = None   # list[str] — all lines
_GUTENBERG_WORD_IDX = None  # dict[str, list[int]] — word → line indices


def _tokenize_for_index(text: str) -> list[str]:
    """Extract lowercase alphabetic tokens for indexing."""
    return re.findall(r"[a-z]{3,}", text.lower())


def _load_gutenberg_corpus() -> tuple[list[str], dict[str, list[int]]]:
    """Load the Gutenberg poetry corpus with an inverted word index."""
    global _GUTENBERG_LINES, _GUTENBERG_WORD_IDX
    if _GUTENBERG_LINES is not None:
        return _GUTENBERG_LINES, _GUTENBERG_WORD_IDX

    if not os.path.exists(GUTENBERG_POETRY_PATH):
        print(f"  Gutenberg poetry corpus not found at {GUTENBERG_POETRY_PATH}")
        _GUTENBERG_LINES = []
        _GUTENBERG_WORD_IDX = {}
        return _GUTENBERG_LINES, _GUTENBERG_WORD_IDX

    from collections import defaultdict
    print(f"  Loading Gutenberg poetry corpus and building index...")
    lines = []
    word_idx = defaultdict(list)

    with open(GUTENBERG_POETRY_PATH) as f:
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line)
                text = obj.get("s", "")
                if text and len(text) > 5:
                    idx = len(lines)
                    lines.append(text)
                    for word in set(_tokenize_for_index(text)):
                        word_idx[word].append(idx)
            except json.JSONDecodeError:
                continue

    _GUTENBERG_LINES = lines
    _GUTENBERG_WORD_IDX = dict(word_idx)
    print(f"  Loaded {len(lines):,} lines, {len(word_idx):,} index terms")
    return _GUTENBERG_LINES, _GUTENBERG_WORD_IDX


def _normalize_for_search(text: str) -> str:
    """Normalize text for matching: lowercase, collapse whitespace, strip punctuation."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def search_gutenberg_line(context: str) -> str | None:
    """
    Search the Gutenberg poetry corpus for a line matching the gap context.

    Uses an inverted word index for fast lookup: find candidate lines that
    share key words with the context, then score and extract the fill.
    """
    lines, word_idx = _load_gutenberg_corpus()
    if not lines:
        return None

    if "[...]" not in context:
        return None

    # Get content words from the context (excluding the gap)
    search_text = context.replace("[...]", " ")
    search_words = _tokenize_for_index(search_text)

    if len(search_words) < 2:
        return None

    # Use the most selective (least common) words to find candidates
    # Sort by frequency in the index — rarest words first
    scored_words = []
    for w in set(search_words):
        if w in word_idx:
            scored_words.append((len(word_idx[w]), w))
    scored_words.sort()

    if not scored_words:
        return None

    # Start with lines matching the rarest word, intersect with others
    # Use top 3 rarest words that appear in the index
    query_words = [w for _, w in scored_words[:3]]

    candidate_idxs = set(word_idx.get(query_words[0], []))
    for w in query_words[1:]:
        candidate_idxs &= set(word_idx.get(w, []))
        if not candidate_idxs:
            break

    if not candidate_idxs:
        # Relax: try just the rarest word
        candidate_idxs = set(word_idx.get(query_words[0], []))

    if not candidate_idxs or len(candidate_idxs) > 500:
        # Too many or zero candidates — skip
        return None

    # Score candidates by how many context words they contain
    context_word_set = set(search_words)
    best_match = None
    best_score = 0

    for idx in candidate_idxs:
        line = lines[idx]
        line_words = set(_tokenize_for_index(line))
        overlap = len(context_word_set & line_words)
        if overlap > best_score:
            best_score = overlap
            best_match = line

    if not best_match or best_score < 2:
        return None

    # Extract the fill from the clean line
    return _extract_fill_from_clean_line(context, best_match)


def _extract_fill_from_clean_line(gap_context: str, clean_line: str) -> str | None:
    """
    Given a gap context like 'thy servant rea[...]s' and a clean line
    like 'A broken ALTAR, Lord, thy servant rears,', extract the fill 'r'.
    """
    parts = gap_context.split("[...]", 1)
    before_raw = parts[0]
    after_raw = parts[1]

    # Get the word fragments immediately around the gap
    # Before: last partial word (chars after last space)
    before_words = before_raw.split()
    after_words = after_raw.split()

    # Use the last 3 words before gap and first 3 after for regex matching
    before_ctx = before_words[-3:] if before_words else []
    after_ctx = after_words[:3] if after_words else []

    # Build pattern: before_words ... (captured fill) ... after_words
    # But we need to handle the partial word around the gap
    # e.g., "servant rea" + [...] + "s," → match "servant rears,"

    # Get the partial word fragments
    prefix_frag = ""
    if before_raw and before_raw[-1] != " ":
        # Last word is a partial — split it
        m = re.search(r"([A-Za-z]+)$", before_raw)
        if m:
            prefix_frag = m.group(1)
            before_ctx = before_raw[:before_raw.rfind(prefix_frag)].split()[-2:]

    suffix_frag = ""
    if after_raw and after_raw[0] != " ":
        m = re.match(r"([A-Za-z]+)", after_raw)
        if m:
            suffix_frag = m.group(1)
            remainder = after_raw[len(suffix_frag):].strip()
            after_ctx = remainder.split()[:2]

    # Build regex for the clean line
    parts_re = []
    if before_ctx:
        parts_re.append(r"\s+".join(re.escape(w) for w in before_ctx))
        parts_re.append(r"\s+")
    if prefix_frag:
        parts_re.append(re.escape(prefix_frag))
    parts_re.append(r"(.+?)")  # The fill
    if suffix_frag:
        parts_re.append(re.escape(suffix_frag))
    if after_ctx:
        parts_re.append(r"\s+")
        parts_re.append(r"\s+".join(re.escape(w) for w in after_ctx))

    pattern = "".join(parts_re)

    match = re.search(pattern, clean_line, re.IGNORECASE)
    if match:
        fill = match.group(1)
        # Sanity check: fill shouldn't be too long
        if len(fill) <= 20:
            return fill

    return None


# ---------------------------------------------------------------------------
# Pass 2: Clean source matching (existing approach)
# ---------------------------------------------------------------------------

# Mapping of TCP IDs to known clean-text URLs
# Extend this as needed — these are the most gap-prone texts
CLEAN_SOURCES = {
    # Herbert — The Temple (Grosart edition, Archive.org OCR)
    "A03058": {
        "local": "corpus/raw/herbert_grosart_clean.txt",
        "source_name": "Grosart edition (Archive.org)",
    },
    # Milton — Paradise Lost
    "A50919": {
        "url": "https://www.gutenberg.org/cache/epub/26/pg26.txt",
        "source_name": "Project Gutenberg #26",
    },
    # Milton — Paradise Regained + Samson Agonistes
    "A50921": {
        "url": "https://www.gutenberg.org/cache/epub/58/pg58.txt",
        "source_name": "Project Gutenberg #58",
    },
    # Spenser — The Faerie Queene
    "A12782": {
        "url": "https://www.gutenberg.org/cache/epub/15272/pg15272.txt",
        "source_name": "Project Gutenberg #15272",
    },
}


def fetch_clean_text(tcp_id: str) -> tuple[str, str] | None:
    """Fetch a clean reference text for the given TCP ID. Returns (text, source_name) or None."""
    if tcp_id not in CLEAN_SOURCES:
        return None

    source = CLEAN_SOURCES[tcp_id]
    source_name = source["source_name"]

    # Local file
    if "local" in source:
        local_path = source["local"]
        if os.path.exists(local_path):
            print(f"  Loading clean text from {local_path} ({source_name}) ...")
            with open(local_path) as f:
                return f.read(), source_name
        else:
            print(f"  Local file not found: {local_path}")
            return None

    # Remote URL
    print(f"  Fetching clean text from {source_name} ...")
    try:
        resp = requests.get(source["url"], timeout=60)
        resp.raise_for_status()
        return resp.text, source_name
    except requests.RequestException as e:
        print(f"  FAILED to fetch clean text: {e}")
        return None


def build_line_index(clean_text: str) -> list[str]:
    """Split clean text into lines for matching."""
    lines = clean_text.split("\n")
    # Strip and filter empty lines but keep structure
    return [line.strip() for line in lines]


def find_matching_context(
    context: str,
    clean_lines: list[str],
    window: int = 5,
    threshold: int = 70,
) -> list[str] | None:
    """
    Find lines in the clean text that match the context surrounding a gap.
    Returns a window of clean lines around the match, or None.
    """
    if not context or len(context) < 10:
        return None

    # Clean up the context — remove [...] placeholder
    search_context = context.replace("[...]", "").strip()
    if len(search_context) < 10:
        return None

    best_score = 0
    best_idx = -1

    for i, line in enumerate(clean_lines):
        if not line:
            continue
        score = fuzz.partial_ratio(search_context, line)
        if score > best_score:
            best_score = score
            best_idx = i

    if best_score >= threshold and best_idx >= 0:
        start = max(0, best_idx - window)
        end = min(len(clean_lines), best_idx + window + 1)
        return clean_lines[start:end]

    return None


def extract_missing_word(
    gap_context: str,
    clean_window: list[str],
) -> str | None:
    """
    Given a gap context like 'the [...] of glory' and a window of clean text,
    try to determine what word(s) fill the gap.
    """
    # Join the clean window into a single string for matching
    clean_block = " ".join(line for line in clean_window if line)

    # Try to match the pattern around the gap
    # Split context at [...] to get before and after
    parts = gap_context.split("[...]")
    if len(parts) != 2:
        return None

    before = parts[0].strip().split()[-3:]  # Last 3 words before gap
    after = parts[1].strip().split()[:3]     # First 3 words after gap

    if not before and not after:
        return None

    # Build a regex to find what's between the before and after words in clean text
    before_pattern = r"\s+".join(re.escape(w) for w in before) if before else ""
    after_pattern = r"\s+".join(re.escape(w) for w in after) if after else ""

    if before_pattern and after_pattern:
        pattern = before_pattern + r"\s+(.+?)\s+" + after_pattern
    elif before_pattern:
        pattern = before_pattern + r"\s+(\S+)"
    elif after_pattern:
        pattern = r"(\S+)\s+" + after_pattern
    else:
        return None

    match = re.search(pattern, clean_block, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    return None


def patch_text_in_stanza(stanza: dict, line_num: int, gap_word: str) -> bool:
    """Replace the first [...] in the specified line with the patched word."""
    if line_num < 1 or line_num > len(stanza["lines"]):
        return False

    idx = line_num - 1
    original = stanza["lines"][idx]
    if "[...]" in original:
        stanza["lines"][idx] = original.replace("[...]", gap_word, 1)
        return True
    return False


def patch_file(json_path: str) -> list[dict]:
    """
    Patch gaps in a single intermediate JSON file.
    Returns a list of patch log entries.
    """
    with open(json_path) as f:
        data = json.load(f)

    tcp_id = data.get("tcp_id", Path(json_path).stem)
    gap_log = data.get("gap_log", [])

    if not gap_log:
        return []

    print(f"\n  {tcp_id}: {len(gap_log)} gaps to patch")

    patches = []
    local_patched = 0

    # --- Pass 1: Local inference for trivial gaps ---
    for gap in gap_log:
        poem_title = gap.get("poem", "")
        stanza_idx = gap.get("stanza", 1) - 1
        line_num = gap.get("line", 1)
        context = gap.get("context", "")
        extent = gap.get("extent", "unknown")

        fill = infer_local_patch(context, extent)
        if fill:
            for poem in data["poems"]:
                if poem.get("title", "") == poem_title:
                    stanzas = poem.get("stanzas", [])
                    if stanza_idx < len(stanzas):
                        if patch_text_in_stanza(stanzas[stanza_idx], line_num, fill):
                            local_patched += 1
                            patches.append({
                                "tcp_id": tcp_id,
                                "poem": poem_title,
                                "stanza": stanza_idx + 1,
                                "line": line_num,
                                "extent": extent,
                                "original_context": context,
                                "clean_source": "local_inference",
                                "status": "patched",
                                "patched_word": fill,
                            })
                    break

    if local_patched:
        print(f"  Pass 1 (local inference): {local_patched} patched")

    # Rebuild gap log with only unpatched gaps
    patched_keys = {(p["poem"], p["stanza"], p["line"]) for p in patches if p["status"] == "patched"}
    remaining_gaps = [
        g for g in gap_log
        if (g.get("poem", ""), g.get("stanza", 1), g.get("line", 1)) not in patched_keys
    ]

    # --- Pass 2: Per-text clean source matching ---
    clean_result = fetch_clean_text(tcp_id)
    pass2_patched = 0
    if clean_result:
        clean_text, source_name = clean_result
        clean_lines = build_line_index(clean_text)
        print(f"  Pass 2 (clean source): {len(remaining_gaps)} gaps, {len(clean_lines)} lines from {source_name}")

        newly_patched = []
        for gap in remaining_gaps:
            poem_title = gap.get("poem", "")
            stanza_idx = gap.get("stanza", 1) - 1
            line_num = gap.get("line", 1)
            context = gap.get("context", "")

            clean_window = find_matching_context(context, clean_lines)
            if clean_window:
                gap_word = extract_missing_word(context, clean_window)
                if gap_word:
                    for poem in data["poems"]:
                        if poem.get("title", "") == poem_title:
                            stanzas = poem.get("stanzas", [])
                            if stanza_idx < len(stanzas):
                                if patch_text_in_stanza(stanzas[stanza_idx], line_num, gap_word):
                                    pass2_patched += 1
                                    patches.append({
                                        "tcp_id": tcp_id,
                                        "poem": poem_title,
                                        "stanza": stanza_idx + 1,
                                        "line": line_num,
                                        "extent": gap.get("extent", "unknown"),
                                        "original_context": context,
                                        "clean_source": source_name,
                                        "status": "patched",
                                        "patched_word": gap_word,
                                    })
                                    newly_patched.append((poem_title, stanza_idx + 1, line_num))
                            break

        if pass2_patched:
            print(f"  Pass 2: {pass2_patched} patched")
    else:
        if remaining_gaps:
            print(f"  Pass 2: No per-text clean source for {tcp_id}")

    # --- Pass 3: Gutenberg Poetry Corpus (universal fallback) ---
    patched_keys = {(p["poem"], p["stanza"], p["line"]) for p in patches if p["status"] == "patched"}
    remaining_gaps = [
        g for g in gap_log
        if (g.get("poem", ""), g.get("stanza", 1), g.get("line", 1)) not in patched_keys
    ]

    pass3_patched = 0
    if remaining_gaps:
        print(f"  Pass 3 (Gutenberg corpus): {len(remaining_gaps)} remaining gaps")
        for gap in remaining_gaps:
            poem_title = gap.get("poem", "")
            stanza_idx = gap.get("stanza", 1) - 1
            line_num = gap.get("line", 1)
            context = gap.get("context", "")

            fill = search_gutenberg_line(context)
            if fill:
                for poem in data["poems"]:
                    if poem.get("title", "") == poem_title:
                        stanzas = poem.get("stanzas", [])
                        if stanza_idx < len(stanzas):
                            if patch_text_in_stanza(stanzas[stanza_idx], line_num, fill):
                                pass3_patched += 1
                                patches.append({
                                    "tcp_id": tcp_id,
                                    "poem": poem_title,
                                    "stanza": stanza_idx + 1,
                                    "line": line_num,
                                    "extent": gap.get("extent", "unknown"),
                                    "original_context": context,
                                    "clean_source": "gutenberg_poetry_corpus",
                                    "status": "patched",
                                    "patched_word": fill,
                                })
                                print(f"    Patched: '{context[:50]}' → '{fill}'")
                        break

        if pass3_patched:
            print(f"  Pass 3: {pass3_patched} patched")

    # Log any still-unresolved gaps
    patched_keys = {(p["poem"], p["stanza"], p["line"]) for p in patches if p["status"] == "patched"}
    for g in gap_log:
        key = (g.get("poem", ""), g.get("stanza", 1), g.get("line", 1))
        if key not in patched_keys:
            patches.append({
                "tcp_id": tcp_id,
                "poem": g.get("poem", ""),
                "stanza": g.get("stanza", 1),
                "line": g.get("line", 1),
                "extent": g.get("extent", "unknown"),
                "original_context": g.get("context", ""),
                "clean_source": "none",
                "status": "unresolved",
                "patched_word": None,
            })

    # Write patched data back
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Summary
    total_patched = sum(1 for p in patches if p["status"] == "patched")
    unresolved = len(patches) - total_patched
    print(f"  Result: {total_patched} patched ({local_patched} local, {pass2_patched} clean source, {pass3_patched} Gutenberg), {unresolved} unresolved")

    return patches


def main():
    config = load_config()
    intermediate_dir = config["paths"]["intermediate"]

    all_patches = []

    if len(sys.argv) > 1:
        # Patch a single file
        json_path = sys.argv[1]
        patches = patch_file(json_path)
        all_patches.extend(patches)
    else:
        # Patch all files with gaps
        json_files = sorted(Path(intermediate_dir).glob("*.json"))
        json_files = [f for f in json_files if not f.name.startswith("_")]
        for json_path in json_files:
            with open(json_path) as f:
                data = json.load(f)
            if data.get("gap_log"):
                patches = patch_file(str(json_path))
                all_patches.extend(patches)

    # Write patch log
    if all_patches:
        log_path = os.path.join(intermediate_dir, "_patch_log.json")
        with open(log_path, "w") as f:
            json.dump(all_patches, f, indent=2, ensure_ascii=False)
        print(f"\nPatch log written to {log_path}")

        # Summary
        patched = sum(1 for p in all_patches if p.get("status") == "patched")
        total = len(all_patches)
        print(f"Overall: {patched}/{total} gaps patched")
        if patched < total:
            print("Review _patch_log.json for unresolved gaps")


if __name__ == "__main__":
    main()
