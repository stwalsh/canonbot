#!/usr/bin/env python3
"""
ingest_to_chroma.py — Embed all JSONL chunks into a ChromaDB persistent collection.

Uses ChromaDB's built-in ONNX all-MiniLM-L6-v2 embeddings (no PyTorch required).
Idempotent: uses chunk_id as document ID, so re-running updates rather than duplicates.

Usage:
    python scripts/ingest_to_chroma.py              # Ingest all chunks
    python scripts/ingest_to_chroma.py --verify      # Verify + run test queries
"""

import json
import os
import sys
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions
import yaml


def load_config(config_path: str = "config/config.yaml") -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


BATCH_SIZE = 100


def ingest_all(chunks_dir: str, chroma_path: str) -> int:
    """Read all JSONL chunks and ingest into ChromaDB. Returns total count."""
    client = chromadb.PersistentClient(path=chroma_path)
    ef = embedding_functions.ONNXMiniLM_L6_V2()
    collection = client.get_or_create_collection(
        name="canon",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    jsonl_files = sorted(Path(chunks_dir).glob("*.jsonl"))
    if not jsonl_files:
        print(f"No JSONL files found in {chunks_dir}")
        return 0

    total = 0
    batch_ids = []
    batch_docs = []
    batch_metas = []
    seen_ids = {}  # chunk_id -> count, for deduplication

    for jsonl_path in jsonl_files:
        with open(jsonl_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                chunk = json.loads(line)

                chunk_id = chunk["chunk_id"]
                chunk_text = chunk["chunk_text"]

                # Ensure unique IDs within each batch and globally
                if chunk_id in seen_ids:
                    seen_ids[chunk_id] += 1
                    chunk_id = f"{chunk_id}-dup{seen_ids[chunk_id]}"
                else:
                    seen_ids[chunk_id] = 0

                # Build metadata — ChromaDB requires string/int/float/bool values
                meta = {
                    "poet": chunk.get("poet", ""),
                    "work": chunk.get("work", ""),
                    "poem_title": chunk.get("poem_title", ""),
                    "date": chunk.get("date", 0),
                    "form": chunk.get("form", ""),
                    "period": chunk.get("period", ""),
                    "chunk_index": chunk.get("chunk_index", 0),
                    "line_range": chunk.get("line_range", ""),
                    "source": chunk.get("source", ""),
                }
                # Optional fields
                if chunk.get("stanza_range"):
                    meta["stanza_range"] = chunk["stanza_range"]
                if chunk.get("tcp_id"):
                    meta["tcp_id"] = chunk["tcp_id"]
                if chunk.get("speaker"):
                    meta["speaker"] = chunk["speaker"]
                # Prose/poetry schema (new chunks only — old chunks won't have these)
                if chunk.get("type"):
                    meta["type"] = chunk["type"]  # "verse" or "prose"
                if chunk.get("genre"):
                    meta["genre"] = chunk["genre"]  # "lyric", "essay", "criticism", etc.

                batch_ids.append(chunk_id)
                batch_docs.append(chunk_text)
                batch_metas.append(meta)
                total += 1

                if len(batch_ids) >= BATCH_SIZE:
                    print(f"  Upserting batch ({total} chunks so far) ...", flush=True)
                    collection.upsert(
                        ids=batch_ids,
                        documents=batch_docs,
                        metadatas=batch_metas,
                    )
                    batch_ids = []
                    batch_docs = []
                    batch_metas = []

    # Flush remaining
    if batch_ids:
        print(f"  Upserting final batch ({total} chunks total) ...", flush=True)
        collection.upsert(
            ids=batch_ids,
            documents=batch_docs,
            metadatas=batch_metas,
        )

    count = collection.count()
    print(f"\nCollection 'canon' now has {count} documents")
    return count


def verify(chroma_path: str):
    """Run verification queries against the collection."""
    client = chromadb.PersistentClient(path=chroma_path)
    ef = embedding_functions.ONNXMiniLM_L6_V2()
    collection = client.get_collection(name="canon", embedding_function=ef)

    count = collection.count()
    print(f"\n{'='*60}")
    print(f"VERIFICATION")
    print(f"{'='*60}")
    print(f"Total documents: {count}")

    # Test 1: Semantic search
    print(f"\n--- Semantic search: 'shall I compare thee to a summer's day' ---")
    results = collection.query(
        query_texts=["shall I compare thee to a summer's day"],
        n_results=3,
    )
    for i, (doc, meta) in enumerate(zip(results["documents"][0], results["metadatas"][0])):
        print(f"  {i+1}. {meta['poet']} — {meta['poem_title']}")
        print(f"     {doc[:100]}...")

    # Test 2: Poet filter
    print(f"\n--- Filter: poet='George Herbert', first 3 ---")
    results = collection.get(
        where={"poet": "George Herbert"},
        limit=3,
    )
    for doc, meta in zip(results["documents"], results["metadatas"]):
        print(f"  {meta['poem_title']}: {doc[:80]}...")

    # Test 3: Period filter
    print(f"\n--- Filter: period='romantic', first 3 ---")
    results = collection.get(
        where={"period": "romantic"},
        limit=3,
    )
    for doc, meta in zip(results["documents"], results["metadatas"]):
        print(f"  {meta['poet']} — {meta['poem_title']}: {doc[:60]}...")

    # Test 4: Semantic + poet filter
    print(f"\n--- Semantic search: 'mortality and time' ---")
    results = collection.query(
        query_texts=["mortality and time"],
        n_results=5,
    )
    for i, (doc, meta) in enumerate(zip(results["documents"][0], results["metadatas"][0])):
        print(f"  {i+1}. {meta['poet']} — {meta['poem_title']} ({meta['period']})")
        print(f"     {doc[:100]}...")

    print(f"\n{'='*60}")


def main():
    config = load_config()
    chunks_dir = config["paths"]["corpus_chunks"]
    chroma_path = "data/chroma"

    os.makedirs(chroma_path, exist_ok=True)

    if "--verify" in sys.argv:
        verify(chroma_path)
    else:
        print(f"Ingesting chunks from {chunks_dir} into {chroma_path} ...")
        count = ingest_all(chunks_dir, chroma_path)
        if count > 0 and "--no-verify" not in sys.argv:
            verify(chroma_path)


if __name__ == "__main__":
    main()
