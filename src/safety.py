"""Content safety filter for retrieved passages.

The corpus spans 1250–1900 and contains passages that are antisemitic,
racist, misogynist, or otherwise harmful by modern standards. This module
flags passages that the bot should not surface, even by quotation.

Design:
  - Retrieval-time keyword scan: fast, catches obvious cases.
  - Does NOT excise from the corpus — passages remain searchable. The filter
    just prevents them from reaching the composition model.
  - Errs on the side of caution: a false positive (skipping a usable passage)
    is far less costly than a false negative (the bot quoting something harmful).
  - Co-occurrence patterns limited to ~200 chars apart to avoid false positives
    from unrelated words in different stanzas of the same chunk.
"""

import re

# ---------------------------------------------------------------------------
# Pattern categories
# ---------------------------------------------------------------------------

# Each pattern is a compiled regex. A passage is flagged if ANY pattern matches.
# Co-occurrence patterns use .{0,200} instead of .*? to limit span.

# Max gap between co-occurring terms (chars)
_G = "{0,200}"

_ANTISEMITIC = [
    rf"\bJew(?:s|ish)?\b.{_G}\b(?:damn|curs|devil|dog|poison|usur|villain|treach|avar|greedy|sly|cunning|blood|kill|crucif)",
    rf"\b(?:damn|curs|devil|dog|poison|villain|treach|avar|greedy|sly|cunning)\b.{_G}\bJew(?:s|ish)?\b",
    r"\bshylock\b",
    r"\bJew(?:'s)?\s+nose\b",
    r"\bcircumcis\w*\s+(?:dog|cur|villain|rogue)",
    r"\bsynagogue\s+of\s+Satan\b",
    rf"\bJew\b.{_G}\bdevil\b",
]

_RACIST = [
    rf"\bnegro(?:es)?\b.{_G}\b(?:slave|savage|beast|brute|inferior|ape|monkey)",
    rf"\b(?:slave|savage|beast|brute|inferior)\b.{_G}\bnegro(?:es)?\b",
    rf"\bblackamoor\b.{_G}\b(?:devil|damn|ugly|beast|lust)",
    rf"\b(?:devil|damn|ugly|beast|lust)\b.{_G}\bblackamoor\b",
    rf"\bMoor\b.{_G}\b(?:devil|damn|thick.?lip|lust|beast|jealous)\b",
    rf"\b(?:thick.?lip|lust|beast)\b.{_G}\bMoor\b",
    rf"\bsavage(?:s)?\b.{_G}\b(?:Indian|native|heathen|cannibal)",
    rf"\b(?:Indian|native|heathen)\b.{_G}\bsavage(?:s)?\b",
]

_MISOGYNIST = [
    # "Frailty, thy name is woman" type — generalising contempt for women as a class
    rf"\b(?:woman|wom[ae]n|wife|wives|female)\b.{_G}\b(?:frailty|weak|fickle|treach|deceit)\b",
    rf"\b(?:frailty|weak|fickle|treach|deceit)\b.{_G}\b(?:woman|wom[ae]n|wife|wives|female)\b",
    # Direct misogynist insults used AS insults (not just period vocabulary)
    rf"\b(?:strumpet|harlot|bawd)\b.{_G}\b(?:she|her|woman|wom[ae]n|wife)\b",
    rf"\b(?:she|her|woman|wom[ae]n|wife)\b.{_G}\b(?:strumpet|harlot|bawd)\b",
]

_ANTI_CATHOLIC = [
    rf"\bpop(?:ish|ery)\b.{_G}\b(?:idol|superstit|antichrist|whore|beast|tyran)",
    r"\bwhore\s+of\s+(?:Babylon|Rome)\b",
    rf"\bRomish\b.{_G}\b(?:idol|superstit|antichrist|tyran)",
]

_ANTI_IRISH = [
    rf"\bIrish\b.{_G}\b(?:savage|wild|barbar|brute|rebel|papist)",
    rf"\b(?:savage|wild|barbar|brute)\b.{_G}\bIrish\b",
]

_ANTI_MUSLIM = [
    rf"\b(?:Turk|Mahometan|Saracen)\b.{_G}\b(?:infidel|barbar|savage|false|cruel|tyran|heathen)",
    rf"\b(?:infidel|barbar|savage|heathen)\b.{_G}\b(?:Turk|Mahometan|Saracen)\b",
]

# Compile all patterns (case-insensitive, dotall for multiline passages)
_ALL_PATTERNS: list[tuple[str, re.Pattern]] = []
for _category, _patterns in [
    ("antisemitic", _ANTISEMITIC),
    ("racist", _RACIST),
    ("misogynist", _MISOGYNIST),
    ("anti-catholic", _ANTI_CATHOLIC),
    ("anti-irish", _ANTI_IRISH),
    ("anti-muslim", _ANTI_MUSLIM),
]:
    for _pat in _patterns:
        _ALL_PATTERNS.append((_category, re.compile(_pat, re.IGNORECASE | re.DOTALL)))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_passage(text: str) -> tuple[bool, str]:
    """Check a passage for potentially harmful content.

    Returns:
        (is_safe, reason) — is_safe is True if the passage is OK to use.
        If flagged, reason describes the category.
    """
    for category, pattern in _ALL_PATTERNS:
        if pattern.search(text):
            return False, category
    return True, ""


def filter_passages(passages: list[dict], log: bool = True) -> list[dict]:
    """Filter a list of passage dicts, removing any with flagged content.

    Args:
        passages: List of passage dicts (must have 'text' key).
        log: If True, print warnings for filtered passages.

    Returns:
        Filtered list with unsafe passages removed.
    """
    safe = []
    for p in passages:
        is_safe, reason = check_passage(p.get("text", ""))
        if is_safe:
            safe.append(p)
        elif log:
            poet = p.get("poet", "?")
            title = p.get("poem_title", "?")
            print(f"  [safety] Filtered: {poet} — \"{title}\" ({reason})")
    return safe
