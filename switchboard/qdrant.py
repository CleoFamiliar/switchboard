"""
qdrant.py — Qdrant overlay for semantic search over jack history.

Stores jack descriptions, notes, and completion context as vectors in
a Qdrant collection, enabling natural language search over past work.

The Qdrant integration is ADDITIVE: the jack graph works without it.
`sw search` falls back to bd search if Qdrant is unavailable.

Collection schema (payload per point):
  {
    "jack_id": "jack-a1b2",
    "title": "...",
    "description": "...",
    "notes": "...",
    "repo": "component-lib",
    "status": "done",
    "type": "jack" | "hold" | ...,
    "updated_at": "<iso timestamp>",
    "commit_msg": "...",       # set on sw done
    "diff_summary": "...",     # set on sw done
    "indexed_at": "<iso>"      # set on sw done
  }

Embedding model: all-MiniLM-L6-v2 (local, no API key needed via sentence-transformers).
Falls back to OpenAI text-embedding-3-small if sentence-transformers is unavailable.
Vector size: 384 for all-MiniLM-L6-v2, 1536 for OpenAI.

Indexing is triggered:
- When a jack is completed via `sw done` (indexes commit msg + diff summary)
- On explicit `sw reindex` (full rebuild from beads graph)
- NOT automatically on every bd operation (too noisy)

The `sw search` command queries the full index, with active-jack context
injected into the query to weight results toward the current workspace.
"""

import hashlib
import json
import subprocess
from typing import Any, Optional

_embed_fn = None
_vector_size = None


def _get_embed_fn():
    """Return an embedding function. Prefers sentence-transformers (local, no API key)."""
    global _embed_fn, _vector_size
    if _embed_fn is not None:
        return _embed_fn

    # Prefer sentence-transformers: local, no API key needed
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")

        def _st_embed(text: str) -> list[float]:
            return model.encode(text).tolist()

        _embed_fn = _st_embed
        _vector_size = 384
        return _embed_fn
    except ImportError:
        pass

    # Fall back to OpenAI if available
    try:
        import openai
        client = openai.OpenAI()

        def _openai_embed(text: str) -> list[float]:
            resp = client.embeddings.create(input=text, model="text-embedding-3-small")
            return resp.data[0].embedding

        _embed_fn = _openai_embed
        _vector_size = 1536
        return _embed_fn
    except (ImportError, Exception):
        pass

    raise ImportError(
        "No embedding backend available. Install one of:\n"
        "  pip install sentence-transformers  # local all-MiniLM-L6-v2 (recommended)\n"
        "  pip install openai                 # text-embedding-3-small (needs OPENAI_API_KEY)"
    )


def get_vector_size() -> int:
    """Return the vector size for the current embedding backend."""
    global _vector_size
    if _vector_size is None:
        embed = _get_embed_fn()
        sample = embed("test")
        _vector_size = len(sample)
    return _vector_size


def _jack_id_to_int(jack_id: str) -> int:
    """Hash a jack ID string to a uint64 for Qdrant point ID."""
    h = hashlib.sha256(jack_id.encode()).hexdigest()
    return int(h[:16], 16)


def _jack_text(jack: dict[str, Any], include_completion: bool = False) -> str:
    """Build the text to embed from a jack dict.

    Base text: title + description + notes.
    With include_completion: also adds commit_msg + diff_summary + decision for done jacks.
    """
    parts = [
        jack.get("title", ""),
        jack.get("description", ""),
        jack.get("notes", ""),
    ]
    if include_completion:
        parts.extend([
            jack.get("commit_msg", ""),
            jack.get("diff_summary", ""),
            jack.get("decision", ""),
        ])
    return " ".join(p for p in parts if p).strip()


def get_client(host: str = "localhost", port: int = 6333):
    """Return a Qdrant client connected to the configured instance.

    Raises qdrant_client.exceptions.UnexpectedResponse if Qdrant is unreachable.
    """
    from qdrant_client import QdrantClient
    return QdrantClient(host=host, port=port)


def ensure_collection(client, collection: str = "switchyard", vector_size: Optional[int] = None) -> None:
    """Create the Qdrant collection if it doesn't exist.

    Uses cosine distance. Defaults to the current backend's vector size.
    """
    from qdrant_client.models import Distance, VectorParams

    if vector_size is None:
        vector_size = get_vector_size()

    collections = [c.name for c in client.get_collections().collections]
    if collection not in collections:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )


def index_jack(client, collection: str, jack: dict[str, Any]) -> None:
    """Embed and upsert a jack into the Qdrant collection.

    Base index: title + description + notes. Used for active and open jacks.
    For completed jacks with commit/diff context, use index_jack_completion instead.
    """
    from qdrant_client.models import PointStruct

    embed = _get_embed_fn()
    text = _jack_text(jack)
    if not text:
        return

    vector = embed(text)
    jack_id = jack.get("id", jack.get("jack_id", "unknown"))
    point_id = _jack_id_to_int(jack_id)

    payload = {
        "jack_id": jack_id,
        "title": jack.get("title", ""),
        "description": jack.get("description", ""),
        "notes": jack.get("notes", ""),
        "repo": jack.get("repo", ""),
        "status": jack.get("status", ""),
        "type": jack.get("issue_type", jack.get("type", "")),
        "updated_at": jack.get("updated_at", ""),
        "commit_msg": jack.get("commit_msg", ""),
        "diff_summary": jack.get("diff_summary", ""),
    }

    client.upsert(
        collection_name=collection,
        points=[PointStruct(id=point_id, vector=vector, payload=payload)],
    )


def index_jack_completion(
    client,
    collection: str,
    jack_id: str,
    commit_msg: str,
    diff_summary: str,
    decision: str = "",
    existing_jack: Optional[dict[str, Any]] = None,
) -> None:
    """Index a jack's completion context (commit message + diff summary + decision).

    Called by `sw done` after a jack is closed. Enriches the jack's vector
    with commit and diff context so future searches surface relevant history.

    If existing_jack is provided, merges completion data into it before indexing.
    Otherwise fetches the jack from beads first.
    """
    import datetime
    from qdrant_client.models import PointStruct

    if existing_jack is None:
        # Try to fetch from beads
        result = subprocess.run(
            ["bd", "show", jack_id, "--json"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            existing_jack = json.loads(result.stdout)
        else:
            existing_jack = {"id": jack_id}

    jack = dict(existing_jack)
    jack["commit_msg"] = commit_msg
    jack["diff_summary"] = diff_summary
    jack["decision"] = decision
    jack["indexed_at"] = datetime.datetime.now().isoformat()

    embed = _get_embed_fn()
    # Embed with full completion context for richer semantic matching
    text = _jack_text(jack, include_completion=True)
    if not text:
        return

    vector = embed(text)
    point_id = _jack_id_to_int(jack_id)

    payload = {
        "jack_id": jack_id,
        "title": jack.get("title", ""),
        "description": jack.get("description", ""),
        "notes": jack.get("notes", ""),
        "repo": jack.get("repo", ""),
        "status": "done",
        "type": jack.get("issue_type", jack.get("type", "")),
        "updated_at": jack.get("updated_at", ""),
        "commit_msg": commit_msg,
        "diff_summary": diff_summary,
        "decision": decision,
        "indexed_at": jack["indexed_at"],
    }

    client.upsert(
        collection_name=collection,
        points=[PointStruct(id=point_id, vector=vector, payload=payload)],
    )


def search(
    client,
    collection: str,
    query: str,
    limit: int = 10,
    repo_filter: Optional[str] = None,
    active_jack_context: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Search the Qdrant collection with a natural language query.

    Embeds the query and returns the top-k most similar jacks. If
    active_jack_context is provided, it is prepended to the query to weight
    results toward the current jack's domain (e.g. repo, current task title).

    Returns a list of payload dicts with a `score` field appended.
    """
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    embed = _get_embed_fn()

    # Weight toward active jack context by prepending it to the query
    effective_query = query
    if active_jack_context:
        effective_query = f"{active_jack_context} {query}"

    query_vector = embed(effective_query)

    query_filter = None
    if repo_filter:
        query_filter = Filter(
            must=[FieldCondition(key="repo", match=MatchValue(value=repo_filter))]
        )

    results = client.query_points(
        collection_name=collection,
        query=query_vector,
        query_filter=query_filter,
        limit=limit,
    )

    hits = []
    for point in results.points:
        payload = dict(point.payload) if point.payload else {}
        payload["score"] = point.score
        hits.append(payload)
    return hits


def search_similar_done_jacks(
    client,
    collection: str,
    query: str,
    exclude_jack_id: str = "",
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Search for similar done jacks that have a non-empty decision field.

    Used by `sw resume` to surface past insights. Returns payload dicts
    for done jacks with decision notes, excluding the current jack.
    """
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    embed = _get_embed_fn()
    query_vector = embed(query)

    # Filter: status=done only
    conditions = [
        FieldCondition(key="status", match=MatchValue(value="done")),
    ]
    query_filter = Filter(must=conditions)

    # Fetch more than limit to allow filtering out empty decisions and self
    results = client.query_points(
        collection_name=collection,
        query=query_vector,
        query_filter=query_filter,
        limit=limit + 5,
    )

    hits = []
    for point in results.points:
        payload = dict(point.payload) if point.payload else {}
        # Skip self and jacks without decision notes
        if payload.get("jack_id") == exclude_jack_id:
            continue
        if not payload.get("decision"):
            continue
        hits.append(payload)
        if len(hits) >= limit:
            break
    return hits


def reindex_all(client, collection: str) -> int:
    """Rebuild the Qdrant collection from the full beads jack graph.

    Fetches all jacks from beads, embeds each one, and upserts into Qdrant.
    Returns the number of jacks indexed. Intended for `sw reindex` command.
    """
    result = subprocess.run(
        ["bd", "list", "--json", "--limit", "0"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"bd list failed: {result.stderr.strip()}")

    jacks = json.loads(result.stdout) if result.stdout.strip() else []

    vector_size = get_vector_size()
    ensure_collection(client, collection, vector_size=vector_size)

    count = 0
    for jack in jacks:
        text = _jack_text(jack)
        if not text:
            continue
        index_jack(client, collection, jack)
        count += 1

    return count
