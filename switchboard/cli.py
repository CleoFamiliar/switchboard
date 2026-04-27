"""
cli.py — Entry point for the `sw` CLI.

Provides subcommands:
  sw status       — summary of open/blocked/pending checkpoints across repos
  sw checkpoint   — manage checkpoint tasks (ack, list, show)
  sw ready        — list tasks with no open blockers (wraps bd ready)
  sw update       — update a task's status or notes (wraps bd update)
  sw search       — semantic search over task history via Qdrant
  sw tree         — render dependency tree with status colours
  sw resume       — re-enter a task mid-stream with full context orientation
  sw state        — update a task's TSO (Task State Object)

All task graph operations delegate to `bd` (beads CLI). Switchboard adds:
- checkpoint ack workflow
- cross-repo filtering
- Qdrant-powered search
- session context injection
- TSO (Task State Object) for structured mid-session handoffs
"""

import os
import datetime
import click
import yaml
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()

# Default location for task state objects
TSO_DIR = Path(os.environ.get("SW_TSO_DIR", Path.home() / ".switchboard" / "state"))


def get_tso_path(task_id: str) -> Path:
    """Return the path to a task's state YAML file."""
    return TSO_DIR / f"{task_id}.yaml"


def load_tso(task_id: str) -> dict:
    """Load a TSO from disk. Returns empty template if not found."""
    path = get_tso_path(task_id)
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {
        "task_id": task_id,
        "goal": None,
        "assumptions": [],
        "hypotheses": [],
        "dead_ends": [],
        "surprises": [],
        "next_action": None,
        "uncertainty": None,
        "updated_at": None,
    }


def save_tso(task_id: str, tso: dict) -> Path:
    """Write a TSO to disk, creating dirs as needed."""
    TSO_DIR.mkdir(parents=True, exist_ok=True)
    path = get_tso_path(task_id)
    tso["updated_at"] = datetime.datetime.now().isoformat()
    with open(path, "w") as f:
        yaml.dump(tso, f, default_flow_style=False, allow_unicode=True)
    return path


@click.group()
@click.version_option()
def main():
    """Switchboard — human-first multi-repo orchestration for AI coding agents."""
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


@main.command()
@click.argument("task_id")
@click.option("--raw", is_flag=True, help="Print raw YAML instead of formatted view")
def resume(task_id, raw):
    """Re-enter a task mid-stream with a structured context orientation.

    Reads the task's TSO (Task State Object) and presents a compact summary
    designed to position an agent correctly for continuation — not just
    inform, but orient.

    The TSO captures: goal, live assumptions, competing hypotheses,
    dead ends (with why), surprises, next action, and uncertainty profile.

    TASK_ID: task identifier (used to locate state YAML)
    """
    tso = load_tso(task_id)

    if raw:
        console.print(yaml.dump(tso, default_flow_style=False))
        return

    if tso.get("updated_at"):
        age = tso["updated_at"]
        header = f"[bold]Task:[/bold] {task_id}  [dim](state last updated: {age})[/dim]"
    else:
        header = f"[bold]Task:[/bold] {task_id}  [dim](no saved state — starting fresh)[/dim]"

    console.print()
    console.print(Panel(header, expand=False, border_style="blue"))

    # Goal
    goal = tso.get("goal")
    console.print(f"\n[bold yellow]Goal[/bold yellow]")
    console.print(f"  {goal or '[not set]'}")

    # Next action — most important thing for re-entry
    next_action = tso.get("next_action")
    console.print(f"\n[bold green]Next action[/bold green]")
    if next_action:
        if isinstance(next_action, dict):
            console.print(f"  {next_action.get('action', '?')}")
            if next_action.get("why"):
                console.print(f"  [dim]why: {next_action['why']}[/dim]")
        else:
            console.print(f"  {next_action}")
    else:
        console.print("  [dim][not set][/dim]")

    # Uncertainty
    uncertainty = tso.get("uncertainty")
    if uncertainty:
        console.print(f"\n[bold red]Uncertainty[/bold red]")
        console.print(f"  {uncertainty}")

    # Live hypotheses
    hypotheses = tso.get("hypotheses") or []
    if hypotheses:
        console.print(f"\n[bold cyan]Live hypotheses[/bold cyan]")
        for h in hypotheses:
            console.print(f"  • {h}")

    # Assumptions
    assumptions = tso.get("assumptions") or []
    if assumptions:
        console.print(f"\n[bold]Assumptions[/bold]")
        for a in assumptions:
            console.print(f"  • {a}")

    # Dead ends
    dead_ends = tso.get("dead_ends") or []
    if dead_ends:
        console.print(f"\n[bold magenta]Dead ends[/bold magenta]")
        for d in dead_ends:
            if isinstance(d, dict):
                console.print(f"  ✗ {d.get('what', d)}")
                if d.get("why"):
                    console.print(f"    [dim]because: {d['why']}[/dim]")
            else:
                console.print(f"  ✗ {d}")

    # Surprises
    surprises = tso.get("surprises") or []
    if surprises:
        console.print(f"\n[bold]Surprises[/bold]")
        for s in surprises:
            console.print(f"  ! {s}")

    console.print()


@main.group()
def state():
    """Manage a task's TSO (Task State Object)."""
    pass


@state.command("set")
@click.argument("task_id")
@click.option("--goal", default=None, help="Set the task goal")
@click.option("--next", "next_action", default=None, help="Next action (format: 'action | why')")
@click.option("--uncertainty", default=None, help="Current uncertainty / open question")
@click.option("--hypothesis", "hypotheses", multiple=True, help="Add a hypothesis (repeatable)")
@click.option("--assumption", "assumptions", multiple=True, help="Add an assumption (repeatable)")
@click.option("--dead-end", "dead_ends", multiple=True, help="Add a dead end: 'what | why' (repeatable)")
@click.option("--surprise", "surprises", multiple=True, help="Add a surprise (repeatable)")
@click.option("--replace", is_flag=True, help="Replace lists instead of appending")
def state_set(task_id, goal, next_action, uncertainty, hypotheses, assumptions, dead_ends, surprises, replace):
    """Update a task's TSO fields.

    By default, hypothesis/assumption/dead-end/surprise options APPEND to
    existing lists. Use --replace to overwrite them instead.

    TASK_ID: task identifier
    """
    tso = load_tso(task_id)

    if goal:
        tso["goal"] = goal

    if next_action:
        parts = next_action.split("|", 1)
        if len(parts) == 2:
            tso["next_action"] = {"action": parts[0].strip(), "why": parts[1].strip()}
        else:
            tso["next_action"] = next_action

    if uncertainty:
        tso["uncertainty"] = uncertainty

    for field, values in [
        ("hypotheses", hypotheses),
        ("assumptions", assumptions),
        ("surprises", surprises),
    ]:
        if values:
            if replace:
                tso[field] = list(values)
            else:
                existing = tso.get(field) or []
                tso[field] = existing + [v for v in values if v not in existing]

    if dead_ends:
        parsed = []
        for d in dead_ends:
            parts = d.split("|", 1)
            if len(parts) == 2:
                parsed.append({"what": parts[0].strip(), "why": parts[1].strip()})
            else:
                parsed.append(d)
        if replace:
            tso["dead_ends"] = parsed
        else:
            existing = tso.get("dead_ends") or []
            tso["dead_ends"] = existing + parsed

    path = save_tso(task_id, tso)
    console.print(f"[green]✓[/green] State saved: {path}")


@state.command("show")
@click.argument("task_id")
def state_show(task_id):
    """Show the raw TSO YAML for a task.

    TASK_ID: task identifier
    """
    tso = load_tso(task_id)
    console.print(yaml.dump(tso, default_flow_style=False))


@state.command("clear")
@click.argument("task_id")
@click.confirmation_option(prompt="Clear all TSO state for this task?")
def state_clear(task_id):
    """Delete the TSO for a task.

    TASK_ID: task identifier
    """
    path = get_tso_path(task_id)
    if path.exists():
        path.unlink()
        console.print(f"[green]✓[/green] Cleared state for {task_id}")
    else:
        console.print(f"[dim]No state found for {task_id}[/dim]")
