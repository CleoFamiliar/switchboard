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

from typing import Any, Optional


def get_client(host: str = "localhost", port: int = 6333):
    """Return a Qdrant client connected to the configured instance.

    Raises qdrant_client.exceptions.UnexpectedResponse if Qdrant is unreachable.
    """
    raise NotImplementedError("get_client: not yet implemented")


def ensure_collection(client, collection: str = "switchyard", vector_size: int = 1536) -> None:
    """Create the Qdrant collection if it doesn't exist.

    Uses cosine distance. Call once at startup before indexing.
    """
    raise NotImplementedError("ensure_collection: not yet implemented")


def index_task(client, collection: str, task: dict[str, Any]) -> None:
    """Embed and upsert a task into the Qdrant collection.

    Embeds the concatenation of title + description + notes, then upserts
    the vector with full task payload. Uses task_id as the point ID (hashed
    to uint64 for Qdrant compatibility).
    """
    raise NotImplementedError("index_task: not yet implemented")


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
    raise NotImplementedError("search: not yet implemented")


def reindex_all(client, collection: str) -> int:
    """Rebuild the Qdrant collection from the full beads task graph.

    Fetches all tasks from beads, embeds each one, and upserts into Qdrant.
    Returns the number of tasks indexed. Intended for `sw reindex` command.
    """
    raise NotImplementedError("reindex_all: not yet implemented")
