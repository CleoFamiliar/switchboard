"""
qdrant.py — Qdrant integration for semantic search over task history.

Stores task descriptions, notes, and checkpoint decisions as vectors in
a Qdrant collection, enabling natural language search over past work.

The Qdrant integration is ADDITIVE: the task graph works without it.
`sw search` simply won't work if Qdrant is unavailable.

Collection schema (payload per point):
  {
    "task_id": "bd-a1b2",
    "title": "...",
    "description": "...",
    "notes": "...",
    "repo": "component-lib",
    "status": "done",
    "type": "task" | "checkpoint" | ...,
    "updated_at": "<iso timestamp>"
  }

Embedding model: text-embedding-3-small (OpenAI) or a local model.
Vector size depends on model — defaults to 1536 for text-embedding-3-small.

Indexing is triggered:
- When a task is created (`sw update --notes` or checkpoint ack)
- On explicit `sw reindex` (full rebuild from beads graph)
- NOT automatically on every bd operation (too noisy)
"""

import hashlib
import json
import subprocess
from typing import Any, Optional

_embed_fn = None


def _get_embed_fn():
    """Return an embedding function. Tries OpenAI first, then sentence-transformers."""
    global _embed_fn
    if _embed_fn is not None:
        return _embed_fn

    # Try OpenAI
    try:
        import openai
        client = openai.OpenAI()

        def _openai_embed(text: str) -> list[float]:
            resp = client.embeddings.create(input=text, model="text-embedding-3-small")
            return resp.data[0].embedding

        _embed_fn = _openai_embed
        return _embed_fn
    except (ImportError, Exception):
        pass

    # Try sentence-transformers
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")

        def _st_embed(text: str) -> list[float]:
            return model.encode(text).tolist()

        _embed_fn = _st_embed
        return _embed_fn
    except ImportError:
        pass

    raise ImportError(
        "No embedding backend available. Install one of:\n"
        "  pip install openai          # for text-embedding-3-small (needs OPENAI_API_KEY)\n"
        "  pip install sentence-transformers  # for local all-MiniLM-L6-v2"
    )


def _task_id_to_int(task_id: str) -> int:
    """Hash a task ID string to a uint64 for Qdrant point ID."""
    h = hashlib.sha256(task_id.encode()).hexdigest()
    return int(h[:16], 16)


def _task_text(task: dict[str, Any]) -> str:
    """Build the text to embed from a task dict."""
    parts = [
        task.get("title", ""),
        task.get("description", ""),
        task.get("notes", ""),
    ]
    return " ".join(p for p in parts if p).strip()


def get_client(host: str = "localhost", port: int = 6333):
    """Return a Qdrant client connected to the configured instance.

    Raises qdrant_client.exceptions.UnexpectedResponse if Qdrant is unreachable.
    """
    from qdrant_client import QdrantClient
    return QdrantClient(host=host, port=port)


def ensure_collection(client, collection: str = "switchyard", vector_size: int = 1536) -> None:
    """Create the Qdrant collection if it doesn't exist.

    Uses cosine distance. Call once at startup before indexing.
    """
    from qdrant_client.models import Distance, VectorParams

    collections = [c.name for c in client.get_collections().collections]
    if collection not in collections:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )


def index_task(client, collection: str, task: dict[str, Any]) -> None:
    """Embed and upsert a task into the Qdrant collection.

    Embeds the concatenation of title + description + notes, then upserts
    the vector with full task payload. Uses task_id as the point ID (hashed
    to uint64 for Qdrant compatibility).
    """
    from qdrant_client.models import PointStruct

    embed = _get_embed_fn()
    text = _task_text(task)
    if not text:
        return

    vector = embed(text)
    point_id = _task_id_to_int(task.get("id", task.get("task_id", "unknown")))

    payload = {
        "task_id": task.get("id", ""),
        "title": task.get("title", ""),
        "description": task.get("description", ""),
        "notes": task.get("notes", ""),
        "repo": task.get("repo", ""),
        "status": task.get("status", ""),
        "type": task.get("issue_type", task.get("type", "")),
        "updated_at": task.get("updated_at", ""),
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
) -> list[dict[str, Any]]:
    """Search the Qdrant collection with a natural language query.

    Embeds the query and returns the top-k most similar tasks, optionally
    filtered by repo. Returns a list of payload dicts with a `score` field.
    """
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    embed = _get_embed_fn()
    query_vector = embed(query)

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


def reindex_all(client, collection: str) -> int:
    """Rebuild the Qdrant collection from the full beads task graph.

    Fetches all tasks from beads, embeds each one, and upserts into Qdrant.
    Returns the number of tasks indexed. Intended for `sw reindex` command.
    """
    result = subprocess.run(
        ["bd", "list", "--json", "--limit", "0"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"bd list failed: {result.stderr.strip()}")

    tasks = json.loads(result.stdout) if result.stdout.strip() else []

    # Detect vector size from the embedding function
    embed = _get_embed_fn()
    sample_vec = embed("test")
    vector_size = len(sample_vec)

    ensure_collection(client, collection, vector_size=vector_size)

    count = 0
    for task in tasks:
        text = _task_text(task)
        if not text:
            continue
        index_task(client, collection, task)
        count += 1

    return count
