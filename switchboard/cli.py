"""
cli.py — Entry point for the `sw` CLI.

Provides subcommands:
  sw status       — summary of open/blocked/pending checkpoints across repos
  sw checkpoint   — manage checkpoint tasks (ack, list, show)
  sw ready        — list tasks with no open blockers (wraps bd ready)
  sw update       — update a task's status or notes (wraps bd update)
  sw search       — text search over tasks (wraps bd search; Qdrant planned)
  sw tree         — render dependency tree with status colours
  sw resume       — re-enter a task mid-stream with full context orientation
  sw state        — update a task's TSO (Task State Object)

All task graph operations delegate to `bd` (beads CLI). Switchboard adds:
- checkpoint ack workflow
- cross-repo filtering
- session context injection
- TSO (Task State Object) for structured mid-session handoffs
"""

import json
import os
import datetime
import subprocess
import click
import yaml
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from rich import box

from .checkpoint import list_open_checkpoints, ack_checkpoint
from .config import load_config

console = Console()

# Default location for task state objects
TSO_DIR = Path(os.environ.get("SW_TSO_DIR", Path.home() / ".switchboard" / "state"))

# Sessions log default path
SESSIONS_LOG = Path("~/.openclaw/workspace/orchestration/sessions.jsonl").expanduser()


def _run_bd(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    """Run a bd CLI command. All bd interactions go through this helper."""
    return subprocess.run(
        ["bd", *args],
        capture_output=True, text=True, check=check,
    )


def _run_bd_json(*args: str) -> list | dict:
    """Run a bd command with --json and parse the output."""
    result = _run_bd(*args, "--json")
    if result.returncode != 0:
        return []
    out = result.stdout.strip()
    if not out:
        return []
    return json.loads(out)


def _try_load_config():
    """Load config, returning None if repos.yaml not found."""
    try:
        return load_config()
    except FileNotFoundError:
        return None


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


# ── Status colors for tree/table rendering ──────────────────────────────────

STATUS_STYLE = {
    "open": "white",
    "in_progress": "blue",
    "blocked": "yellow",
    "deferred": "dim",
    "closed": "green",
    "checkpoint": "magenta",
}


def _status_color(status: str) -> str:
    return STATUS_STYLE.get(status, "white")


# ── CLI Group ────────────────────────────────────────────────────────────────

@click.group()
@click.version_option()
def main():
    """Switchboard — human-first multi-repo orchestration for AI coding agents."""
    pass


# ── sw status ────────────────────────────────────────────────────────────────

@main.command()
@click.option("--repo", "-r", default=None, help="Filter by repo ID")
def status(repo):
    """Show workspace summary: open, blocked, and pending checkpoints.

    Queries the beads task graph and formats a human-readable dashboard
    showing counts by status, any checkpoints awaiting ack, and the next
    ready tasks per repo.
    """
    # Get overall stats from bd status --json
    stats = _run_bd_json("status")
    summary = stats.get("summary", stats) if isinstance(stats, dict) else {}

    # Build status table
    table = Table(
        title="Workspace Status",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
    )
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right")

    table.add_row("Open", str(summary.get("open_issues", 0)))
    table.add_row("In Progress", str(summary.get("in_progress_issues", 0)))
    table.add_row("Blocked", str(summary.get("blocked_issues", 0)))
    table.add_row("Deferred", str(summary.get("deferred_issues", 0)))
    table.add_row("Ready", str(summary.get("ready_issues", 0)))
    table.add_row("Closed", str(summary.get("closed_issues", 0)))
    table.add_row("Total", str(summary.get("total_issues", 0)))
    console.print(table)

    # Show open checkpoints
    checkpoints = list_open_checkpoints(repo_filter=repo)
    if checkpoints:
        console.print(f"\n[bold magenta]Pending Checkpoints ({len(checkpoints)})[/bold magenta]")
        for cp in checkpoints:
            console.print(f"  [{cp.id}] {cp.title}  [dim](requires: {cp.requires})[/dim]")
    else:
        console.print("\n[dim]No pending checkpoints.[/dim]")

    # Show ready tasks (top 5)
    ready_cmd = ["ready", "--limit", "5"]
    if repo:
        ready_cmd.extend(["--label", repo])
    ready_tasks = _run_bd_json(*ready_cmd)
    if ready_tasks:
        console.print(f"\n[bold green]Ready to Work ({len(ready_tasks)} shown)[/bold green]")
        for t in ready_tasks:
            tid = t.get("id", "?")
            title = t.get("title", "")
            prio = t.get("priority", "?")
            console.print(f"  [green]{tid}[/green]  P{prio}  {title}")
    else:
        console.print("\n[dim]No tasks ready.[/dim]")


# ── sw checkpoint ────────────────────────────────────────────────────────────

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
    config = _try_load_config()
    sessions_log = config.sessions_log_path if config else SESSIONS_LOG
    actor = os.environ.get("USER", "unknown")
    session_id = os.environ.get("CLAUDE_SESSION_ID")

    try:
        ack_checkpoint(task_id, decision, actor, session_id, sessions_log)
        console.print(f"[green]Checkpoint {task_id} acknowledged.[/green]")
        console.print(f"  Decision: {decision}")
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)


@checkpoint.command("list")
def checkpoint_list():
    """List all open checkpoint tasks requiring acknowledgment.

    Shows checkpoints sorted by priority, with who is required to ack
    and what prompt/decision is needed.
    """
    checkpoints = list_open_checkpoints()
    if not checkpoints:
        console.print("[dim]No open checkpoints.[/dim]")
        return

    table = Table(
        title="Open Checkpoints",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
    )
    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("Requires", style="magenta")
    table.add_column("Prompt", max_width=50)

    for cp in checkpoints:
        table.add_row(cp.id, cp.title, cp.requires, cp.prompt[:80] if cp.prompt else "")
    console.print(table)


# ── sw ready ─────────────────────────────────────────────────────────────────

@main.command()
@click.option("--repo", "-r", default=None, help="Filter by repo ID")
@click.option("--actor", "-a", default=None, help="Filter by actor (kale/cleo)")
def ready(repo, actor):
    """List tasks with no open blockers — ready to be claimed and worked.

    Wraps `bd ready` and adds repo + actor filtering. In multi-repo mode,
    groups ready tasks by repo.
    """
    # bd ready --json gives us blocker-aware ready tasks
    cmd = ["ready", "--limit", "20"]
    if actor:
        cmd.extend(["--assignee", actor])
    if repo:
        cmd.extend(["--label", repo])

    tasks = _run_bd_json(*cmd)
    if not tasks:
        console.print("[dim]No tasks ready. Everything is blocked or done.[/dim]")
        return

    table = Table(
        title="Ready Tasks",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
    )
    table.add_column("ID", style="cyan")
    table.add_column("P", justify="center", style="yellow")
    table.add_column("Type", style="dim")
    table.add_column("Title")
    table.add_column("Assignee", style="blue")

    for t in tasks:
        table.add_row(
            t.get("id", "?"),
            str(t.get("priority", "?")),
            t.get("issue_type", ""),
            t.get("title", ""),
            t.get("assignee") or "",
        )
    console.print(table)


# ── sw update ────────────────────────────────────────────────────────────────

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
    cmd = ["update", task_id]

    if claim:
        cmd.append("--claim")
    if status:
        cmd.extend(["--status", status])
    if notes:
        cmd.extend(["--notes", notes])

    if not claim and not status and not notes:
        console.print("[yellow]Nothing to update. Use --status, --notes, or --claim.[/yellow]")
        return

    result = _run_bd(*cmd)
    if result.returncode != 0:
        console.print(f"[red]Error:[/red] {result.stderr.strip()}")
        raise SystemExit(1)

    console.print(f"[green]Updated {task_id}.[/green]")
    if result.stdout.strip():
        console.print(result.stdout.strip())

    # Log to session JSONL
    config = _try_load_config()
    sessions_log = config.sessions_log_path if config else SESSIONS_LOG
    _log_update_event(sessions_log, task_id, status, notes, claim)


def _log_update_event(sessions_log: Path, task_id: str, status, notes, claim):
    """Append an update event to the sessions JSONL."""
    sessions_log.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "event": "task_update",
        "task_id": task_id,
        "status": status,
        "notes": notes,
        "claimed": claim,
        "actor": os.environ.get("USER", "unknown"),
        "session_id": os.environ.get("CLAUDE_SESSION_ID"),
        "timestamp": datetime.datetime.now().isoformat(),
    }
    with open(sessions_log, "a") as f:
        f.write(json.dumps(event) + "\n")


# ── sw search ────────────────────────────────────────────────────────────────

@main.command()
@click.argument("query")
@click.option("--limit", "-l", default=10, help="Number of results")
@click.option("--repo", "-r", default=None, help="Filter by repo ID")
def search(query, limit, repo):
    """Search tasks by text query.

    Wraps `bd search` for text-based search over task titles and IDs.
    Qdrant-powered semantic search will be added in a future release.

    QUERY: search query string
    """
    # Text search via bd search (Qdrant integration deferred — see qdrant.py)
    cmd = ["search", query, "--limit", str(limit)]
    if repo:
        cmd.extend(["--label", repo])

    result = _run_bd(*cmd, "--json")
    if result.returncode != 0:
        # bd search may not support --json; fall back to plain output
        result = _run_bd("search", query, "--limit", str(limit))
        if result.returncode != 0:
            console.print(f"[red]Search failed:[/red] {result.stderr.strip()}")
            raise SystemExit(1)
        if result.stdout.strip():
            console.print(result.stdout.strip())
        else:
            console.print("[dim]No results.[/dim]")
        return

    tasks = json.loads(result.stdout) if result.stdout.strip() else []
    if not tasks:
        console.print("[dim]No results.[/dim]")
        return

    table = Table(
        title=f'Search: "{query}"',
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
    )
    table.add_column("ID", style="cyan")
    table.add_column("Status")
    table.add_column("P", justify="center", style="yellow")
    table.add_column("Title")

    for t in tasks:
        st = t.get("status", "?")
        color = _status_color(st)
        table.add_row(
            t.get("id", "?"),
            f"[{color}]{st}[/{color}]",
            str(t.get("priority", "?")),
            t.get("title", ""),
        )
    console.print(table)


# ── sw tree ──────────────────────────────────────────────────────────────────

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
    # Get all tasks (including closed to show full tree)
    cmd = ["list", "--flat", "--limit", "0"]
    if repo:
        cmd.extend(["--label", repo])
    tasks = _run_bd_json(*cmd)

    if not tasks:
        console.print("[dim]No tasks found.[/dim]")
        return

    # Build lookup by ID
    task_map = {t["id"]: t for t in tasks}

    # Get dependency info for each task: fetch deps in batch
    # bd dep list supports batch IDs
    dep_graph = {}  # task_id -> list of dependency IDs (what it depends on)
    child_graph = {}  # task_id -> list of task IDs that depend on it

    all_ids = list(task_map.keys())
    for tid in all_ids:
        dep_graph[tid] = []
        child_graph[tid] = []

    # Fetch deps for all tasks. bd dep list can take multiple IDs.
    if all_ids:
        dep_result = _run_bd("dep", "list", *all_ids, "--json")
        if dep_result.returncode == 0 and dep_result.stdout.strip():
            deps = json.loads(dep_result.stdout)
            for d in deps:
                # dep record: from_id depends on to_id
                from_id = d.get("from_id") or d.get("issue_id", "")
                to_id = d.get("to_id") or d.get("depends_on", "")
                if from_id and to_id:
                    dep_graph.setdefault(from_id, []).append(to_id)
                    child_graph.setdefault(to_id, []).append(from_id)

    # Find root tasks (no dependencies, or deps are all outside our set)
    roots = [
        tid for tid in all_ids
        if not dep_graph.get(tid)
    ]

    # If no clear roots, show all tasks as roots (flat fallback)
    if not roots:
        roots = all_ids

    # Render with rich.tree
    rich_tree = Tree("[bold]Switchboard Task Graph[/bold]")
    rendered = set()

    def _add_node(parent_tree: Tree, tid: str, current_depth: int):
        if depth is not None and current_depth > depth:
            return
        if tid in rendered:
            # Show reference to already-rendered node
            t = task_map.get(tid, {})
            st = t.get("status", "?")
            color = _status_color(st)
            parent_tree.add(f"[dim]-> {tid}[/dim] [dim](see above)[/dim]")
            return
        rendered.add(tid)

        t = task_map.get(tid, {})
        st = t.get("status", "?")
        prio = t.get("priority", "?")
        title = t.get("title", "")
        itype = t.get("issue_type", "")
        color = _status_color(st)

        # Format: [ID] P# type: title (status)
        label = f"[{color}]{tid}[/{color}]  P{prio}  "
        if itype:
            label += f"[dim]{itype}:[/dim] "
        label += f"{title}  [{color}]({st})[/{color}]"

        node = parent_tree.add(label)

        # Recurse into children (tasks that depend on this one)
        children = child_graph.get(tid, [])
        for child_id in children:
            if child_id in task_map:
                _add_node(node, child_id, current_depth + 1)

    for root_id in roots:
        _add_node(rich_tree, root_id, 0)

    console.print(rich_tree)


# ── sw resume ────────────────────────────────────────────────────────────────

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


# ── sw state ─────────────────────────────────────────────────────────────────

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
