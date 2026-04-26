"""
cli.py — Entry point for the `sw` CLI.

Provides subcommands:
  sw status       — summary of open/blocked/pending checkpoints across repos
  sw checkpoint   — manage checkpoint tasks (ack, list, show)
  sw ready        — list tasks with no open blockers (wraps bd ready)
  sw update       — update a task's status or notes (wraps bd update)
  sw search       — semantic search over task history via Qdrant
  sw tree         — render dependency tree with status colours

All task graph operations delegate to `bd` (beads CLI). Switchyard adds:
- checkpoint ack workflow
- cross-repo filtering
- Qdrant-powered search
- session context injection
"""

import click
from rich.console import Console

console = Console()


@click.group()
@click.version_option()
def main():
    """Switchyard — human-first multi-repo orchestration for AI coding agents."""
    pass


@main.command()
@click.option("--repo", "-r", default=None, help="Filter by repo ID")
def status(repo):
    """Show workspace summary: open, blocked, and pending checkpoints.

    Queries the beads task graph and formats a human-readable dashboard
    showing counts by status, any checkpoints awaiting ack, and the next
    ready tasks per repo.
    """
    raise NotImplementedError("sw status: not yet implemented")


@main.group()
def checkpoint():
    """Manage checkpoint tasks."""
    pass


@checkpoint.command("ack")
@click.argument("task_id")
@click.argument("decision")
def checkpoint_ack(task_id, decision):
    """Acknowledge a checkpoint, unblocking downstream tasks.

    Sets the checkpoint task to done, records the decision note, and
    marks all directly-blocked downstream tasks as open. Logs the ack
    to the session JSONL for audit trail.

    TASK_ID: beads task ID (e.g. bd-a1b2)
    DECISION: short note explaining the decision made at this checkpoint
    """
    raise NotImplementedError("sw checkpoint ack: not yet implemented")


@checkpoint.command("list")
def checkpoint_list():
    """List all open checkpoint tasks requiring acknowledgment.

    Shows checkpoints sorted by priority, with who is required to ack
    and what prompt/decision is needed.
    """
    raise NotImplementedError("sw checkpoint list: not yet implemented")


@main.command()
@click.option("--repo", "-r", default=None, help="Filter by repo ID")
@click.option("--actor", "-a", default=None, help="Filter by actor (kale/cleo)")
def ready(repo, actor):
    """List tasks with no open blockers — ready to be claimed and worked.

    Wraps `bd ready` and adds repo + actor filtering. In multi-repo mode,
    groups ready tasks by repo.
    """
    raise NotImplementedError("sw ready: not yet implemented")


@main.command()
@click.argument("task_id")
@click.option("--status", "-s", default=None, help="New status")
@click.option("--notes", "-n", default=None, help="Progress/completion notes")
@click.option("--claim", is_flag=True, help="Atomically claim the task")
def update(task_id, status, notes, claim):
    """Update a task's status, notes, or claim it.

    Thin wrapper around `bd update` that also stamps the current session ID
    onto the task and logs the update to the session JSONL.

    TASK_ID: beads task ID (e.g. bd-a1b2)
    """
    raise NotImplementedError("sw update: not yet implemented")


@main.command()
@click.argument("query")
@click.option("--limit", "-l", default=10, help="Number of results")
@click.option("--repo", "-r", default=None, help="Filter by repo ID")
def search(query, limit, repo):
    """Semantic search over task history and decisions via Qdrant.

    Embeds the query and searches the Qdrant collection for similar task
    descriptions, notes, and checkpoint decisions. Useful for surfacing
    related past work before starting something new.

    QUERY: natural language search query
    """
    raise NotImplementedError("sw search: not yet implemented")


@main.command()
@click.option("--repo", "-r", default=None, help="Filter by repo ID")
@click.option("--depth", "-d", default=None, type=int, help="Max tree depth")
def tree(repo, depth):
    """Render the dependency tree with status colours.

    Fetches the full task graph from beads and renders it as an ASCII tree
    using rich, colour-coded by status (open=white, claimed=blue,
    blocked=yellow, checkpoint=magenta, done=green).

    Shows cross-repo `triggers_update` relations with a distinct marker.
    """
    raise NotImplementedError("sw tree: not yet implemented")
