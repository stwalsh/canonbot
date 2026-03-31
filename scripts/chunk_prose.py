#!/usr/bin/env python3
"""
chunk_prose.py — Convert prose intermediate JSON into JSONL chunks for ChromaDB.

Unlike chunk_corpus.py (which handles verse stanzas), this works with prose
paragraphs. Chunks are 300-500 tokens, grouping short paragraphs and splitting
long ones.

Output includes type/genre metadata for the prose/poetry schema.

Usage:
    python scripts/chunk_prose.py                    # chunk all PROSE_*.json files
    python scripts/chunk_prose.py PROSE_hazlitt.json  # chunk one file
"""

import json
import re
import sys
from pathlib import Path

INTERMEDIATE_DIR = Path("corpus/intermediate")
CHUNKS_DIR = Path("corpus/chunks")

MIN_TOKENS = 80
MAX_TOKENS = 500
TARGET_TOKENS = 350


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


def _slugify(text: str, max_len: int = 30) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:max_len].rstrip("-")


def chunk_essay(essay: dict, author: str, date: str, source_file: str,
                content_type: str, genre: str) -> list[dict]:
    """Chunk a single essay into ~300-500 token chunks."""
    title = essay["title"]
    collection = essay.get("collection", "")
    paragraphs = essay["paragraphs"]

    # Build chunk ID prefix
    author_slug = _slugify(author)
    title_slug = _slugify(title)

    chunks = []
    current_text = ""
    chunk_index = 0

    for para in paragraphs:
        candidate = (current_text + "\n\n" + para).strip() if current_text else para

        if _estimate_tokens(candidate) > MAX_TOKENS and current_text:
            # Flush current chunk
            chunk_id = f"{author_slug}-{title_slug}-{chunk_index:03d}"
            chunks.append({
                "chunk_id": chunk_id,
                "chunk_text": current_text,
                "poet": author,  # keeping field name for compat
                "poem_title": title,  # keeping field name for compat
                "work": collection or title,
                "date": date,
                "period": "",
                "form": "prose",
                "source": source_file,
                "chunk_index": chunk_index,
                "line_range": "",
                "type": content_type,
                "genre": genre,
            })
            chunk_index += 1
            current_text = para
        else:
            current_text = candidate

    # Flush remaining
    if current_text and _estimate_tokens(current_text) >= MIN_TOKENS:
        chunk_id = f"{author_slug}-{title_slug}-{chunk_index:03d}"
        chunks.append({
            "chunk_id": chunk_id,
            "chunk_text": current_text,
            "poet": author,
            "poem_title": title,
            "work": collection or title,
            "date": date,
            "period": "",
            "form": "prose",
            "source": source_file,
            "chunk_index": chunk_index,
            "type": content_type,
            "genre": genre,
        })
    elif current_text and chunks:
        # Too short on its own — append to previous chunk
        chunks[-1]["chunk_text"] += "\n\n" + current_text

    return chunks


def chunk_prose_file(json_path: Path) -> list[dict]:
    """Chunk all essays in a prose intermediate JSON file."""
    with open(json_path) as f:
        data = json.load(f)

    author = data["author"]
    date = str(data.get("date", ""))
    source_file = data.get("source_file", json_path.name)
    content_type = data.get("type", "prose")
    genre = data.get("genre", "essay")

    all_chunks = []
    for essay in data["essays"]:
        chunks = chunk_essay(essay, author, date, source_file, content_type, genre)
        all_chunks.extend(chunks)

    return all_chunks


def main():
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

    # Find prose intermediate files
    if len(sys.argv) > 1:
        files = [INTERMEDIATE_DIR / sys.argv[1]]
    else:
        files = sorted(INTERMEDIATE_DIR.glob("PROSE_*.json"))

    if not files:
        print("No PROSE_*.json files found in corpus/intermediate/")
        return

    for json_path in files:
        if not json_path.exists():
            print(f"  File not found: {json_path}")
            continue

        print(f"  Chunking {json_path.name}...")
        chunks = chunk_prose_file(json_path)

        # Write JSONL — use author slug as filename with prose_ prefix
        with open(json_path) as f:
            data = json.load(f)
        slug = _slugify(data["author"])
        output_path = CHUNKS_DIR / f"prose_{slug}.jsonl"

        with open(output_path, "w") as f:
            for chunk in chunks:
                f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

        print(f"    {len(chunks)} chunks -> {output_path}")

        # Summary
        essays = set(c["poem_title"] for c in chunks)
        print(f"    {len(essays)} essays, avg {sum(len(c['chunk_text']) for c in chunks) // max(len(chunks), 1)} chars/chunk")


if __name__ == "__main__":
    main()
