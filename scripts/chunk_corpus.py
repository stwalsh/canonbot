#!/usr/bin/env python3
"""
chunk_corpus.py — Convert intermediate JSON into stanza-level JSONL chunks.

Chunking strategy:
- Sonnets (14 lines): 1 chunk each
- Short lyrics (< ~300 tokens): 1 chunk each
- Stanzaic poems: 1 chunk per stanza; groups 2-3 short stanzas to hit 50-300 token target
- Long blank verse: split at verse paragraph (lg) boundaries; split very long ones at sentence ends
- Target: 50-300 tokens per chunk

Output: JSONL files in corpus/chunks/, one per poet.

Usage:
    python scripts/chunk_corpus.py
"""

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import tiktoken
import yaml

# Lazy-load the tokenizer
_ENC = None


def get_encoder():
    global _ENC
    if _ENC is None:
        _ENC = tiktoken.encoding_for_model("gpt-4")
    return _ENC


def count_tokens(text: str) -> int:
    return len(get_encoder().encode(text))


def load_config(config_path: str = "config/config.yaml") -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug for chunk IDs."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:60].rstrip("-")


def detect_form(poem: dict) -> str:
    """Detect poetic form from structure."""
    stanzas = poem.get("stanzas", [])
    total_lines = sum(len(s["lines"]) for s in stanzas)

    # Single stanza of 14 lines → sonnet
    if len(stanzas) == 1 and total_lines == 14:
        return "sonnet"

    # Check if all stanzas are couplets (2 lines each)
    if stanzas and all(len(s["lines"]) == 2 for s in stanzas):
        return "couplet"

    # One big stanza (blank verse / verse paragraph)
    if len(stanzas) == 1 and total_lines > 20:
        return "blank_verse"

    # Short poem, likely a song or epigram
    if total_lines <= 8 and len(stanzas) <= 2:
        div_type = poem.get("div_type", "").lower()
        if "song" in div_type:
            return "song"
        if "epigram" in div_type:
            return "epigram"
        return "other"

    # Stanzaic (multiple stanzas)
    if len(stanzas) > 1:
        return "stanzaic"

    return "other"


def detect_period(date_str: str) -> str:
    """Derive period from publication date."""
    try:
        year = int(re.search(r"\d{4}", str(date_str)).group())
    except (AttributeError, ValueError):
        return "early_modern"  # Default for EEBO-TCP

    if year < 1500:
        return "medieval"
    if year < 1660:
        return "early_modern"
    if year < 1700:
        return "restoration"
    if year < 1790:
        return "augustan"
    if year < 1830:
        return "romantic"
    return "victorian"


def parse_date(date_str: str) -> int:
    """Extract a 4-digit year from a date string."""
    try:
        match = re.search(r"\d{4}", str(date_str))
        if match:
            return int(match.group())
    except (ValueError, AttributeError):
        pass
    return 0


def normalize_author(author: str) -> str:
    """Convert 'Herbert, George, 1593-1633.' to 'George Herbert'."""
    if "," in author:
        parts = [p.strip().rstrip(".") for p in author.split(",")]
        # TCP format: "Surname, Firstname, dates." — drop date-like parts and honorifics
        name_parts = [p for p in parts if p and not re.match(
            r"^\d|^attributed|^Sir|^Mrs|^Mr|^Duke|^Earl|^fl\.|^Baron|^Viscount|^Lord",
            p,
        )]
        if len(name_parts) >= 2:
            # Surname is first, given name is second
            result = f"{name_parts[1]} {name_parts[0]}"
        elif name_parts:
            result = name_parts[0]
        else:
            result = author.strip()
    else:
        result = author.strip()

    # Canonical aliases for poets with inconsistent TCP metadata
    return _AUTHOR_ALIASES.get(result, result)


# Map variant normalized names to canonical form
_AUTHOR_ALIASES = {
    "Henry Howard Surrey": "Henry Howard",
    "Henry Howard Earl of Surrey": "Henry Howard",
    # Tennyson: various forms from different sources
    "Alfred": "Alfred Lord Tennyson",
    "Alfred Tennyson Lord Tennyson": "Alfred Lord Tennyson",
    "Alfred Tennyson Baron Tennyson": "Alfred Lord Tennyson",
    # Byron: Delphi gives full name, EEBO/OBEV give shorter
    "George Gordon": "Lord Byron",
    "George Gordon Byron Lord Byron": "Lord Byron",
    "George Gordon Lord Byron": "Lord Byron",
    # Cowley: popebot couplets use bare surname
    "Cowley": "Abraham Cowley",
    # Rochester: multiple variants across sources
    "John Wilmot": "John Wilmot, Earl of Rochester",
    "John Wilmot Rochester": "John Wilmot, Earl of Rochester",
    "John Wilmot Earl of Rochester": "John Wilmot, Earl of Rochester",
    "Earl of Rochester": "John Wilmot, Earl of Rochester",
    # Rossetti: Delphi uses full middle name
    "Christina Georgina Rossetti": "Christina Rossetti",
    # Yeats: Delphi anthology vs Delphi Poets Series
    "William Butler Yeats": "W. B. Yeats",
    # Martial: R. Fletcher's 1656 translation, miscredited in TCP metadata
    "Martial.": "R. Fletcher",
    "Martial": "R. Fletcher",
    # Sidney: TCP drops the Sir
    "Philip Sidney": "Sir Philip Sidney",
    # Prelude misattribution from Delphi
    "Two Book Prelude: Book II": "William Wordsworth",
}


def author_slug(author: str) -> str:
    """Get a short slug from author name for filenames.

    Expects an already-normalized name like 'George Herbert'.
    Takes the last word (surname) as the slug.
    """
    # Don't re-normalize — caller should pass already-normalized name
    name = author.strip()
    return slugify(name.split()[-1]) if name else "unknown"


def stanza_text(stanza: dict) -> str:
    """Join stanza lines into text."""
    return "\n".join(stanza.get("lines", []))


def chunk_sonnet(poem: dict, metadata: dict) -> list[dict]:
    """A sonnet is always one chunk."""
    text = "\n".join(
        line
        for stanza in poem["stanzas"]
        for line in stanza["lines"]
    )
    return [make_chunk(metadata, poem, text, 0, "1-14", "1")]


def chunk_short_lyric(poem: dict, metadata: dict) -> list[dict]:
    """Short poems become one chunk."""
    text = "\n".join(
        line
        for stanza in poem["stanzas"]
        for line in stanza["lines"]
    )
    total_lines = sum(len(s["lines"]) for s in poem["stanzas"])
    return [make_chunk(metadata, poem, text, 0, f"1-{total_lines}", f"1-{len(poem['stanzas'])}")]


def chunk_stanzaic(poem: dict, metadata: dict, min_tokens: int, max_tokens: int) -> list[dict]:
    """
    Chunk stanzaic poems: one chunk per stanza, but group short stanzas
    together to meet the minimum token target.
    """
    chunks = []
    buffer_stanzas = []
    buffer_lines = []
    buffer_start_line = 1
    buffer_start_stanza = 1
    current_line = 1

    for i, stanza in enumerate(poem["stanzas"], 1):
        stanza_lines = stanza.get("lines", [])
        buffer_stanzas.append(i)
        buffer_lines.extend(stanza_lines)

        combined_text = "\n".join(buffer_lines)
        tokens = count_tokens(combined_text)

        # Flush if we've hit the target range or this stanza alone is large enough
        if tokens >= min_tokens or i == len(poem["stanzas"]):
            # If way over max, split at this stanza boundary anyway
            if tokens > max_tokens and len(buffer_stanzas) > 1:
                # Flush everything except the current stanza
                prev_lines = buffer_lines[:-len(stanza_lines)]
                prev_text = "\n".join(prev_lines)
                end_line = current_line + len(prev_lines) - 1
                stanza_range = f"{buffer_start_stanza}-{buffer_stanzas[-2]}" if len(buffer_stanzas) > 2 else str(buffer_start_stanza)
                chunks.append(make_chunk(
                    metadata, poem, prev_text, len(chunks),
                    f"{buffer_start_line}-{end_line}", stanza_range,
                ))
                # Start fresh with current stanza
                buffer_start_line = end_line + 1
                buffer_start_stanza = i
                buffer_lines = list(stanza_lines)
                buffer_stanzas = [i]
            else:
                end_line = current_line + len(buffer_lines) - 1
                stanza_range = f"{buffer_start_stanza}-{i}" if len(buffer_stanzas) > 1 else str(i)
                chunks.append(make_chunk(
                    metadata, poem, combined_text, len(chunks),
                    f"{buffer_start_line}-{end_line}", stanza_range,
                ))
                buffer_start_line = end_line + 1
                buffer_start_stanza = i + 1
                buffer_lines = []
                buffer_stanzas = []

        current_line_for_tracking = current_line
        current_line += len(stanza_lines) if not buffer_lines or i == len(poem["stanzas"]) else 0

    # Flush any remaining
    if buffer_lines:
        text = "\n".join(buffer_lines)
        total = sum(len(s["lines"]) for s in poem["stanzas"])
        stanza_range = f"{buffer_start_stanza}-{len(poem['stanzas'])}" if len(buffer_stanzas) > 1 else str(buffer_start_stanza)
        chunks.append(make_chunk(
            metadata, poem, text, len(chunks),
            f"{buffer_start_line}-{total}", stanza_range,
        ))

    return chunks


def chunk_blank_verse(poem: dict, metadata: dict, max_tokens: int) -> list[dict]:
    """
    Split long blank verse at stanza/verse-paragraph boundaries.
    If a single verse paragraph is too long, split at sentence-ending lines.
    """
    chunks = []
    current_line = 1

    for i, stanza in enumerate(poem["stanzas"], 1):
        lines = stanza.get("lines", [])
        text = "\n".join(lines)
        tokens = count_tokens(text)

        if tokens <= max_tokens:
            end_line = current_line + len(lines) - 1
            chunks.append(make_chunk(
                metadata, poem, text, len(chunks),
                f"{current_line}-{end_line}", str(i),
            ))
            current_line = end_line + 1
        else:
            # Split at sentence-ending lines (lines ending with . ! ? ;)
            sub_chunks = split_at_sentences(lines, max_tokens)
            for sub_lines in sub_chunks:
                sub_text = "\n".join(sub_lines)
                end_line = current_line + len(sub_lines) - 1
                chunks.append(make_chunk(
                    metadata, poem, sub_text, len(chunks),
                    f"{current_line}-{end_line}", str(i),
                ))
                current_line = end_line + 1

    return chunks


def split_at_sentences(lines: list[str], max_tokens: int) -> list[list[str]]:
    """Split a list of lines into groups at sentence-ending boundaries."""
    groups = []
    current = []

    for line in lines:
        current.append(line)
        text = "\n".join(current)

        if count_tokens(text) >= max_tokens and line.rstrip().endswith((".", "!", "?", ";")):
            groups.append(current)
            current = []

    if current:
        groups.append(current)

    return groups


def make_chunk(
    metadata: dict,
    poem: dict,
    text: str,
    index: int,
    line_range: str,
    stanza_range: str,
) -> dict:
    """Build a chunk dict matching the metadata schema."""
    poet = normalize_author(metadata.get("author", "Unknown"))
    work = metadata.get("title", "Unknown")
    poem_title = poem.get("title", "untitled")
    date = parse_date(metadata.get("date", ""))
    form = detect_form(poem) if index == 0 else poem.get("_form", "other")

    # Cache form on the poem for subsequent chunks
    if index == 0:
        poem["_form"] = form

    poet_slug = slugify(poet)
    work_slug = slugify(work)[:20]
    poem_slug = slugify(poem_title)[:20] if poem_title else "untitled"

    chunk_id = f"{poet_slug}-{work_slug}-{poem_slug}-{index:03d}"

    chunk = {
        "chunk_id": chunk_id,
        "poet": poet,
        "work": work,
        "poem_title": poem_title,
        "date": date,
        "form": form,
        "period": detect_period(metadata.get("date", "")),
        "themes": [],
        "chunk_text": text,
        "chunk_index": index,
        "line_range": line_range,
        "stanza_range": stanza_range,
        "source": metadata.get("source", "EEBO-TCP"),
        "tcp_id": metadata.get("tcp_id", ""),
    }
    speaker = poem.get("speaker", "")
    if speaker:
        chunk["speaker"] = speaker
    return chunk


def chunk_poem(poem: dict, metadata: dict, config: dict) -> list[dict]:
    """Route a poem to the appropriate chunking strategy."""
    chunking = config.get("chunking", {})
    min_tokens = chunking.get("min_tokens", 50)
    max_tokens = chunking.get("max_tokens", 300)
    short_max = chunking.get("short_lyric_max_tokens", 300)

    stanzas = poem.get("stanzas", [])
    if not stanzas:
        return []

    total_lines = sum(len(s["lines"]) for s in stanzas)
    total_text = "\n".join(
        line for stanza in stanzas for line in stanza["lines"]
    )
    total_tokens = count_tokens(total_text)
    form = detect_form(poem)
    poem["_form"] = form

    # Sonnet
    if form == "sonnet":
        return chunk_sonnet(poem, metadata)

    # Short lyric — entire poem fits in one chunk
    if total_tokens <= short_max:
        return chunk_short_lyric(poem, metadata)

    # Blank verse / single long stanza
    if form == "blank_verse" or (len(stanzas) == 1 and total_tokens > max_tokens):
        return chunk_blank_verse(poem, metadata, max_tokens)

    # Stanzaic
    return chunk_stanzaic(poem, metadata, min_tokens, max_tokens)


def process_file(json_path: str, config: dict) -> list[dict]:
    """Process a single intermediate JSON file into chunks."""
    with open(json_path) as f:
        data = json.load(f)

    metadata = {
        "tcp_id": data.get("tcp_id", ""),
        "author": data.get("author", "Unknown"),
        "title": data.get("title", "Unknown"),
        "date": data.get("date", ""),
        "source": data.get("source", "EEBO-TCP"),
    }

    chunks = []
    poems = data.get("poems", [])

    # Work-level poem filtering: keep only specified poem indices
    keep_poems = config.get("keep_poems_only", {})
    file_key = data.get("tcp_id", "") or Path(json_path).stem
    if file_key in keep_poems:
        allowed = set(keep_poems[file_key])
        dropped = len(poems) - len(allowed)
        poems = [p for i, p in enumerate(poems) if i in allowed]
        # (dropped count logged by caller)

    # Tottel's Miscellany (A03742): re-attribute by section
    # Surrey: poems 0-39, Wyatt: 40-136, Grimald: 137-176, Uncertain: 177+
    # We keep only Surrey and Wyatt.
    if data.get("tcp_id") == "A03742":
        for i, poem in enumerate(poems):
            if i < 40:
                meta = {**metadata, "author": "Howard, Henry, Earl of Surrey, 1517?-1547."}
            elif i < 137:
                meta = {**metadata, "author": "Wyatt, Thomas, Sir, 1503?-1542."}
            else:
                continue  # Skip Grimald and uncertain authors
            chunks.extend(chunk_poem(poem, meta, config))
        return chunks

    for poem in poems:
        chunks.extend(chunk_poem(poem, metadata, config))

    return chunks


def validate_chunks(all_chunks: dict[str, list[dict]]) -> None:
    """Run validation and print stats."""
    print("\n" + "=" * 70)
    print("VALIDATION & STATISTICS")
    print("=" * 70)

    total_chunks = 0
    total_tokens = 0
    token_counts = []
    seen_texts = set()
    duplicates = 0
    remaining_gaps = 0
    poet_stats = defaultdict(lambda: {"chunks": 0, "works": set()})

    for poet, chunks in sorted(all_chunks.items()):
        for chunk in chunks:
            total_chunks += 1
            tokens = count_tokens(chunk["chunk_text"])
            total_tokens += tokens
            token_counts.append(tokens)

            # Track duplicates
            text_hash = hash(chunk["chunk_text"])
            if text_hash in seen_texts:
                duplicates += 1
            seen_texts.add(text_hash)

            # Track remaining gaps
            if "[...]" in chunk["chunk_text"]:
                remaining_gaps += 1

            # Track per-poet stats
            poet_name = chunk["poet"]
            poet_stats[poet_name]["chunks"] += 1
            poet_stats[poet_name]["works"].add(chunk["work"])

    # Summary table
    print(f"\n{'Poet':<30} {'Chunks':>8} {'Works':>8}")
    print("-" * 50)
    for poet, stats in sorted(poet_stats.items()):
        print(f"{poet:<30} {stats['chunks']:>8} {len(stats['works']):>8}")
    print("-" * 50)
    print(f"{'TOTAL':<30} {total_chunks:>8}")

    # Token distribution
    if token_counts:
        token_counts.sort()
        print(f"\nToken count distribution:")
        print(f"  Min:    {token_counts[0]}")
        print(f"  25th:   {token_counts[len(token_counts) // 4]}")
        print(f"  Median: {token_counts[len(token_counts) // 2]}")
        print(f"  75th:   {token_counts[3 * len(token_counts) // 4]}")
        print(f"  Max:    {token_counts[-1]}")
        print(f"  Mean:   {total_tokens / len(token_counts):.0f}")

        # Flag outliers
        outliers_small = [t for t in token_counts if t < 20]
        outliers_large = [t for t in token_counts if t > 500]
        if outliers_small:
            print(f"\n  WARNING: {len(outliers_small)} chunks under 20 tokens")
        if outliers_large:
            print(f"  WARNING: {len(outliers_large)} chunks over 500 tokens")

    # Issues
    print(f"\nDuplicates: {duplicates}")
    print(f"Remaining gaps ([...]): {remaining_gaps}")

    if duplicates == 0 and remaining_gaps == 0:
        print("\nAll clear.")
    print("=" * 70)


def main():
    config = load_config()
    intermediate_dir = config["paths"]["intermediate"]
    chunks_dir = config["paths"]["corpus_chunks"]
    os.makedirs(chunks_dir, exist_ok=True)

    json_files = sorted(Path(intermediate_dir).glob("*.json"))
    # Skip the patch log
    json_files = [f for f in json_files if f.name != "_patch_log.json"]

    if not json_files:
        print(f"No intermediate JSON files found in {intermediate_dir}")
        return

    # Exclusion list
    exclude_poets = config.get("exclude_poets", [])

    # Group chunks by poet
    poet_chunks: dict[str, list[dict]] = defaultdict(list)
    excluded_count = 0

    # Work-level exclusion lists
    exclude_tcp_ids = set(config.get("exclude_tcp_ids", []))
    keep_poems_only = config.get("keep_poems_only", {})

    for json_path in json_files:
        # Check if this file should be excluded entirely (stem matches tcp_id for EEBO)
        stem = json_path.stem
        if stem in exclude_tcp_ids:
            print(f"  Skipping {stem} (excluded)")
            continue

        print(f"  Chunking {stem} ...", end=" ", flush=True)
        chunks = process_file(str(json_path), config)
        print(f"{len(chunks)} chunks")

        for chunk in chunks:
            poet = chunk.get("poet", "unknown")
            # Skip excluded poets
            if any(ex in poet for ex in exclude_poets):
                excluded_count += 1
                continue
            poet_key = author_slug(poet)
            poet_chunks[poet_key].append(chunk)

    if excluded_count:
        print(f"\n  Excluded {excluded_count} chunks from non-canonical poets")

    # Deduplicate: drop chunks with identical text, keep first occurrence
    seen_texts: set[int] = set()
    dedup_dropped = 0
    for poet_key in list(poet_chunks.keys()):
        kept = []
        for chunk in poet_chunks[poet_key]:
            text_hash = hash(chunk["chunk_text"])
            if text_hash in seen_texts:
                dedup_dropped += 1
                continue
            seen_texts.add(text_hash)
            kept.append(chunk)
        poet_chunks[poet_key] = kept

    print(f"\n  Deduplication: dropped {dedup_dropped} duplicate chunks")

    # Write JSONL files, one per poet
    for poet_key, chunks in sorted(poet_chunks.items()):
        if not chunks:
            continue
        out_path = os.path.join(chunks_dir, f"{poet_key}.jsonl")
        with open(out_path, "w") as f:
            for chunk in chunks:
                f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
        print(f"  Wrote {len(chunks)} chunks to {out_path}")

    # Validation
    validate_chunks(poet_chunks)


if __name__ == "__main__":
    main()
