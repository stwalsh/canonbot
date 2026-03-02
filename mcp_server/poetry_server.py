#!/usr/bin/env python3
"""
poetry_server.py — MCP server exposing the Canon Bot poetry corpus.

Tools for semantic search, poet/period browsing, context retrieval, and serendipity.
Resources for corpus stats and poet metadata.

Run:
    python -m mcp_server.poetry_server
"""

import json
import random
from collections import Counter, defaultdict

import chromadb
from chromadb.utils import embedding_functions
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

mcp = FastMCP("Poetry Corpus")

CHROMA_PATH = "data/chroma"
COLLECTION_NAME = "canon"

_client = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=CHROMA_PATH)
        ef = embedding_functions.ONNXMiniLM_L6_V2()
        _collection = _client.get_collection(
            name=COLLECTION_NAME,
            embedding_function=ef,
        )
    return _collection


def _format_result(doc: str, meta: dict) -> dict:
    """Format a single search result for return."""
    return {
        "chunk_id": meta.get("chunk_id", ""),
        "poet": meta.get("poet", ""),
        "work": meta.get("work", ""),
        "poem_title": meta.get("poem_title", ""),
        "date": meta.get("date", 0),
        "period": meta.get("period", ""),
        "form": meta.get("form", ""),
        "line_range": meta.get("line_range", ""),
        "stanza_range": meta.get("stanza_range", ""),
        "text": doc,
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def search_poems(query: str, limit: int = 5) -> list[dict]:
    """Semantic vector search across the full poetry corpus.

    Search for poems by meaning, theme, imagery, or quoted text.
    Returns the most relevant passages with full metadata.

    Args:
        query: Natural language search query (e.g. "mortality and time",
               "shall I compare thee to a summer's day", "pastoral love")
        limit: Maximum results to return (default 5, max 20)
    """
    limit = min(limit, 20)
    col = _get_collection()
    results = col.query(
        query_texts=[query],
        n_results=limit,
        include=["documents", "metadatas"],
    )
    return [
        _format_result(doc, meta)
        for doc, meta in zip(results["documents"][0], results["metadatas"][0])
    ]


@mcp.tool()
def search_by_poet(poet: str, query: str | None = None, limit: int = 10) -> list[dict]:
    """Browse or search within a single poet's work.

    With query: semantic search filtered to this poet.
    Without query: returns chunks ordered by work and position.

    Args:
        poet: Poet name (e.g. "George Herbert", "John Keats"). Case-sensitive.
        query: Optional semantic search within this poet's work.
        limit: Maximum results (default 10, max 50)
    """
    limit = min(limit, 50)
    col = _get_collection()

    if query:
        results = col.query(
            query_texts=[query],
            n_results=limit,
            where={"poet": poet},
            include=["documents", "metadatas"],
        )
        return [
            _format_result(doc, meta)
            for doc, meta in zip(results["documents"][0], results["metadatas"][0])
        ]
    else:
        results = col.get(
            where={"poet": poet},
            limit=limit,
            include=["documents", "metadatas"],
        )
        return [
            _format_result(doc, meta)
            for doc, meta in zip(results["documents"], results["metadatas"])
        ]


@mcp.tool()
def search_by_period(period: str, query: str | None = None, limit: int = 10) -> list[dict]:
    """Browse or search within a historical period.

    Periods: medieval, early_modern, restoration, augustan, romantic, victorian.

    Args:
        period: Historical period (e.g. "romantic", "early_modern")
        query: Optional semantic search within this period.
        limit: Maximum results (default 10, max 50)
    """
    limit = min(limit, 50)
    col = _get_collection()

    if query:
        results = col.query(
            query_texts=[query],
            n_results=limit,
            where={"period": period},
            include=["documents", "metadatas"],
        )
        return [
            _format_result(doc, meta)
            for doc, meta in zip(results["documents"][0], results["metadatas"][0])
        ]
    else:
        results = col.get(
            where={"period": period},
            limit=limit,
            include=["documents", "metadatas"],
        )
        return [
            _format_result(doc, meta)
            for doc, meta in zip(results["documents"], results["metadatas"])
        ]


@mcp.tool()
def get_poem_context(chunk_id: str) -> list[dict]:
    """Get a chunk plus its neighbours (surrounding stanzas from the same poem).

    Returns the requested chunk and adjacent chunks (chunk_index ± 1)
    from the same poem, giving surrounding context.

    Args:
        chunk_id: The chunk_id to look up (e.g. "george-herbert-the-temple-sacr-the-altar-000")
    """
    col = _get_collection()

    # Get the target chunk
    target = col.get(ids=[chunk_id], include=["documents", "metadatas"])
    if not target["documents"]:
        return [{"error": f"Chunk '{chunk_id}' not found"}]

    meta = target["metadatas"][0]
    poet = meta.get("poet", "")
    poem_title = meta.get("poem_title", "")
    chunk_index = meta.get("chunk_index", 0)

    # Find neighbouring chunks from the same poem
    where_filter = {
        "$and": [
            {"poet": poet},
            {"poem_title": poem_title},
        ]
    }
    neighbours = col.get(
        where=where_filter,
        limit=100,
        include=["documents", "metadatas"],
    )

    # Filter to chunk_index ± 1
    context = []
    for doc, nmeta in zip(neighbours["documents"], neighbours["metadatas"]):
        idx = nmeta.get("chunk_index", -1)
        if abs(idx - chunk_index) <= 1:
            context.append(_format_result(doc, nmeta))

    # Sort by chunk_index
    context.sort(key=lambda x: x.get("line_range", ""))
    return context


@mcp.tool()
def list_poets() -> list[dict]:
    """List all poets in the corpus with chunk counts and works.

    Returns a summary of every poet: name, number of chunks, list of works,
    and periods represented.
    """
    col = _get_collection()

    # Get all metadata (no documents needed)
    all_data = col.get(include=["metadatas"])

    poet_info = defaultdict(lambda: {"chunks": 0, "works": set(), "periods": set()})
    for meta in all_data["metadatas"]:
        poet = meta.get("poet", "Unknown")
        poet_info[poet]["chunks"] += 1
        poet_info[poet]["works"].add(meta.get("work", ""))
        poet_info[poet]["periods"].add(meta.get("period", ""))

    return [
        {
            "poet": poet,
            "chunks": info["chunks"],
            "works": sorted(info["works"]),
            "periods": sorted(info["periods"]),
        }
        for poet, info in sorted(poet_info.items())
    ]


@mcp.tool()
def random_passage(poet: str | None = None, form: str | None = None) -> dict:
    """Get a random poetry passage for serendipitous discovery.

    Optionally filter by poet or poetic form.

    Args:
        poet: Optional poet name to filter by.
        form: Optional form (sonnet, couplet, stanzaic, blank_verse, song, ode, elegy, epigram, other)
    """
    col = _get_collection()

    where_clauses = []
    if poet:
        where_clauses.append({"poet": poet})
    if form:
        where_clauses.append({"form": form})

    if len(where_clauses) > 1:
        where_filter = {"$and": where_clauses}
    elif where_clauses:
        where_filter = where_clauses[0]
    else:
        where_filter = None

    # Get a batch and pick randomly
    total = col.count()
    offset = random.randint(0, max(0, total - 100))
    results = col.get(
        where=where_filter,
        limit=100,
        offset=offset,
        include=["documents", "metadatas"],
    )

    if not results["documents"]:
        # Try without offset
        results = col.get(
            where=where_filter,
            limit=100,
            include=["documents", "metadatas"],
        )

    if not results["documents"]:
        return {"error": "No matching passages found"}

    idx = random.randint(0, len(results["documents"]) - 1)
    return _format_result(results["documents"][idx], results["metadatas"][idx])


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@mcp.resource("corpus://stats")
def corpus_stats() -> str:
    """Corpus statistics: total chunks, poets, period/form distributions."""
    col = _get_collection()
    all_data = col.get(include=["metadatas"])

    total = len(all_data["metadatas"])
    poets = set()
    periods = Counter()
    forms = Counter()

    for meta in all_data["metadatas"]:
        poets.add(meta.get("poet", "Unknown"))
        periods[meta.get("period", "unknown")] += 1
        forms[meta.get("form", "unknown")] += 1

    stats = {
        "total_chunks": total,
        "total_poets": len(poets),
        "periods": dict(periods.most_common()),
        "forms": dict(forms.most_common()),
    }
    return json.dumps(stats, indent=2)


@mcp.resource("poets://{name}")
def poet_metadata(name: str) -> str:
    """Metadata for a specific poet: works, periods, chunk count."""
    col = _get_collection()
    results = col.get(
        where={"poet": name},
        include=["metadatas"],
    )

    if not results["metadatas"]:
        return json.dumps({"error": f"Poet '{name}' not found"})

    works = set()
    periods = set()
    forms = Counter()
    dates = set()

    for meta in results["metadatas"]:
        works.add(meta.get("work", ""))
        periods.add(meta.get("period", ""))
        forms[meta.get("form", "")] += 1
        if meta.get("date"):
            dates.add(meta["date"])

    info = {
        "poet": name,
        "chunks": len(results["metadatas"]),
        "works": sorted(works),
        "periods": sorted(periods),
        "forms": dict(forms.most_common()),
        "dates": sorted(dates),
    }
    return json.dumps(info, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
