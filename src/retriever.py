"""ChromaDB retrieval interface for the canon bot brain."""

import chromadb
from chromadb.utils import embedding_functions

CHROMA_PATH = "data/chroma"
COLLECTION_NAME = "canon"

_client = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is None:
        try:
            _client = chromadb.PersistentClient(path=CHROMA_PATH)
            ef = embedding_functions.ONNXMiniLM_L6_V2()
            _collection = _client.get_collection(
                name=COLLECTION_NAME,
                embedding_function=ef,
            )
        except Exception as e:
            print(f"  [retriever] ERROR: ChromaDB unavailable: {e}")
            raise
    return _collection


def search(
    query: str,
    n_results: int = 5,
    exclude_ids: set[str] | None = None,
    exclude_poets: set[str] | None = None,
    content_type: str | None = None,
    max_prose: int | None = None,
) -> list[dict]:
    """Semantic search over the corpus.

    Args:
        query: Text to match against.
        n_results: How many results to return.
        exclude_ids: Chunk IDs to skip (anti-repetition).
        exclude_poets: Poet names to filter out (cooling).
        content_type: Filter by "verse" or "prose". None = all.
        max_prose: Max prose results to include. None = no limit.
            Untagged chunks (pre-schema) are treated as verse.

    Returns:
        List of passage dicts with keys: chunk_id, text, poet, work,
        poem_title, date, period, form, line_range, distance, type, genre.
    """
    col = _get_collection()
    # Fetch extra if we need to filter some out
    n_exclude = len(exclude_ids) if exclude_ids else 0
    n_cool = (len(exclude_poets) * 3) if exclude_poets else 0
    n_prose_buffer = (n_results * 2) if max_prose is not None else 0
    fetch_n = n_results + n_exclude + n_cool + n_prose_buffer

    # ChromaDB where filter for content_type
    where = None
    if content_type:
        where = {"type": content_type}

    results = col.query(query_texts=[query], n_results=fetch_n, where=where)

    # Normalise exclude_poets to lowercase for comparison
    cool_poets = {p.lower() for p in exclude_poets} if exclude_poets else set()

    passages = []
    prose_count = 0
    for i in range(len(results["ids"][0])):
        chunk_id = results["ids"][0][i]
        if exclude_ids and chunk_id in exclude_ids:
            continue
        meta = results["metadatas"][0][i]
        if cool_poets and meta.get("poet", "").lower() in cool_poets:
            continue

        # Prose limiting: untagged chunks treated as verse
        chunk_type = meta.get("type", "verse")
        if max_prose is not None and chunk_type == "prose":
            if prose_count >= max_prose:
                continue
            prose_count += 1

        passages.append({
            "chunk_id": chunk_id,
            "text": results["documents"][0][i],
            "poet": meta.get("poet", ""),
            "work": meta.get("work", ""),
            "poem_title": meta.get("poem_title", ""),
            "date": meta.get("date", ""),
            "period": meta.get("period", ""),
            "form": meta.get("form", ""),
            "line_range": meta.get("line_range", ""),
            "distance": results["distances"][0][i],
            "type": chunk_type,
            "genre": meta.get("genre", ""),
        })
        if len(passages) >= n_results:
            break

    return passages


def search_multi(
    queries: list[str],
    n_results: int = 5,
    exclude_ids: set[str] | None = None,
    exclude_poets: set[str] | None = None,
    content_type: str | None = None,
    max_prose: int | None = None,
) -> list[dict]:
    """Run multiple semantic searches and merge results.

    Deduplicates by chunk_id, keeping the best (lowest) distance for each.
    Returns top n_results by distance.
    """
    best: dict[str, dict] = {}  # chunk_id -> passage dict (with best distance)

    for query in queries:
        # Fetch more per query to get good coverage after dedup
        hits = search(
            query, n_results=n_results, exclude_ids=exclude_ids,
            exclude_poets=exclude_poets, content_type=content_type,
            max_prose=max_prose,
        )
        for p in hits:
            cid = p["chunk_id"]
            if cid not in best or p["distance"] < best[cid]["distance"]:
                best[cid] = p

    ranked = sorted(best.values(), key=lambda p: p["distance"])
    return ranked[:n_results]
