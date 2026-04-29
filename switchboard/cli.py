"""
cli.py — Entry point for the `sw` CLI.

Provides subcommands:
  sw status       — summary of open/blocked/pending holds across repos
  sw checkpoint   — manage hold jacks (ack, list, show)
  sw ready        — list jacks with no open blockers (wraps bd ready)
  sw update       — update a jack's status or notes (wraps bd update)
  sw done         — mark a jack done and index completion context
  sw search       — search jacks (Qdrant semantic + bd text fallback)
  sw tree         — render patch cord (dependency) tree with status colours
  sw resume       — re-enter a jack mid-stream with full context orientation
  sw state        — update a jack's TSO (Jack State Object)
  sw mode         — show or change workspace mode (prototype/deliberate)

All jack graph operations delegate to `bd` (beads CLI). Switchboard adds:
- hold ack workflow
- cross-repo filtering
- session context injection
- TSO (Jack State Object) for structured mid-session handoffs
- prototype mode for faster iteration (auto-ack holds with requires:any)

Operators: Kale (human) or Cleo (AI agent).
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

from .checkpoint import list_open_holds, ack_hold
from .config import load_config

console = Console()

# Default location for jack state objects (TSO)
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


def get_tso_path(jack_id: str) -> Path:
    """Return the path to a jack's state YAML file."""
    return TSO_DIR / f"{jack_id}.yaml"


def load_tso(jack_id: str) -> dict:
    """Load a TSO from disk. Returns empty template if not found."""
    path = get_tso_path(jack_id)
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {
        "jack_id": jack_id,
        "goal": None,
        "assumptions": [],
        "hypotheses": [],
        "dead_ends": [],
        "surprises": [],
        "next_action": None,
        "uncertainty": None,
        "updated_at": None,
    }


def save_tso(jack_id: str, tso: dict) -> Path:
    """Write a TSO to disk, creating dirs as needed."""
    TSO_DIR.mkdir(parents=True, exist_ok=True)
    path = get_tso_path(jack_id)
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
    """Show workspace summary: open, blocked, and pending holds.

    Queries the beads jack graph and formats a human-readable dashboard
    showing counts by status, any holds awaiting ack, and the next
    ready jacks per repo.
    """
    # Show current mode
    config = _try_load_config()
    current_mode = config.mode if config else "deliberate"
    if current_mode == "prototype":
        console.print(f"[bold]Mode:[/bold] [yellow]{current_mode}[/yellow]  [dim](holds with requires:any auto-ack)[/dim]")
    else:
        console.print(f"[bold]Mode:[/bold] [green]{current_mode}[/green]")
    console.print()

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

    # Show open holds (filter by mode)
    holds = list_open_holds(repo_filter=repo)
    if current_mode == "prototype":
        # In prototype mode, auto-skip holds with requires:any
        blocking_holds = [h for h in holds if h.requires == "kale"]
        auto_acked = len(holds) - len(blocking_holds)
        if blocking_holds:
            console.print(f"\n[bold magenta]Pending Holds ({len(blocking_holds)})[/bold magenta]")
            for h in blocking_holds:
                console.print(f"  [{h.id}] {h.title}  [dim](requires: {h.requires})[/dim]")
        if auto_acked:
            console.print(f"\n[dim]{auto_acked} hold(s) with requires:any auto-acked in prototype mode[/dim]")
        if not blocking_holds and not auto_acked:
            console.print("\n[dim]No pending holds.[/dim]")
    else:
        if holds:
            console.print(f"\n[bold magenta]Pending Holds ({len(holds)})[/bold magenta]")
            for h in holds:
                console.print(f"  [{h.id}] {h.title}  [dim](requires: {h.requires})[/dim]")
        else:
            console.print("\n[dim]No pending holds.[/dim]")

    # Show ready jacks (top 5)
    ready_cmd = ["ready", "--limit", "5"]
    if repo:
        ready_cmd.extend(["--label", repo])
    ready_jacks = _run_bd_json(*ready_cmd)
    if ready_jacks:
        console.print(f"\n[bold green]Ready to Work ({len(ready_jacks)} shown)[/bold green]")
        for j in ready_jacks:
            jid = j.get("id", "?")
            title = j.get("title", "")
            prio = j.get("priority", "?")
            console.print(f"  [green]{jid}[/green]  P{prio}  {title}")
    else:
        console.print("\n[dim]No jacks ready.[/dim]")


# ── sw checkpoint ────────────────────────────────────────────────────────────

@main.group()
def checkpoint():
    """Manage holds (checkpoint jacks)."""
    pass


@checkpoint.command("ack")
@click.argument("jack_id")
@click.argument("decision")
def checkpoint_ack(jack_id, decision):
    """Acknowledge a hold, unblocking downstream jacks.

    Sets the hold jack to done, records the decision note, and
    marks all directly-blocked downstream jacks as open. Logs the ack
    to the session JSONL for audit trail.

    JACK_ID: beads jack ID (e.g. jack-a1b2)
    DECISION: short note explaining the decision made at this hold
    """
    config = _try_load_config()
    sessions_log = config.sessions_log_path if config else SESSIONS_LOG
    actor = os.environ.get("USER", "unknown")
    session_id = os.environ.get("CLAUDE_SESSION_ID")

    try:
        ack_hold(jack_id, decision, actor, session_id, sessions_log)
        console.print(f"[green]Hold {jack_id} acknowledged.[/green]")
        console.print(f"  Decision: {decision}")
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)


@checkpoint.command("list")
def checkpoint_list():
    """List all open holds requiring acknowledgment.

    Shows holds sorted by priority, with who is required to ack
    and what prompt/decision is needed.
    """
    config = _try_load_config()
    current_mode = config.mode if config else "deliberate"

    holds = list_open_holds()
    if not holds:
        console.print("[dim]No open holds.[/dim]")
        return

    # In prototype mode, note which holds are auto-acked
    if current_mode == "prototype":
        blocking = [h for h in holds if h.requires == "kale"]
        auto = [h for h in holds if h.requires != "kale"]
        if auto:
            console.print(f"[dim]{len(auto)} hold(s) with requires:any auto-acked in prototype mode[/dim]\n")
        holds = blocking
        if not holds:
            console.print("[dim]No blocking holds (all auto-acked in prototype mode).[/dim]")
            return

    table = Table(
        title="Open Holds",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
    )
    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("Requires", style="magenta")
    table.add_column("Prompt", max_width=50)

    for h in holds:
        table.add_row(h.id, h.title, h.requires, h.prompt[:80] if h.prompt else "")
    console.print(table)


# ── sw ready ─────────────────────────────────────────────────────────────────

@main.command()
@click.option("--repo", "-r", default=None, help="Filter by repo ID")
@click.option("--actor", "-a", default=None, help="Filter by operator (kale/cleo)")
def ready(repo, actor):
    """List jacks with no open blockers — ready to be claimed and worked.

    Wraps `bd ready` and adds repo + operator filtering. In multi-repo mode,
    groups ready jacks by repo.
    """
    # bd ready --json gives us blocker-aware ready jacks
    cmd = ["ready", "--limit", "20"]
    if actor:
        cmd.extend(["--assignee", actor])
    if repo:
        cmd.extend(["--label", repo])

    jacks = _run_bd_json(*cmd)
    if not jacks:
        console.print("[dim]No jacks ready. Everything is blocked or done.[/dim]")
        return

    table = Table(
        title="Ready Jacks",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
    )
    table.add_column("ID", style="cyan")
    table.add_column("P", justify="center", style="yellow")
    table.add_column("Type", style="dim")
    table.add_column("Title")
    table.add_column("Assignee", style="blue")

    for j in jacks:
        table.add_row(
            j.get("id", "?"),
            str(j.get("priority", "?")),
            j.get("issue_type", ""),
            j.get("title", ""),
            j.get("assignee") or "",
        )
    console.print(table)


# ── sw update ────────────────────────────────────────────────────────────────

@main.command()
@click.argument("jack_id")
@click.option("--status", "-s", default=None, help="New status")
@click.option("--notes", "-n", default=None, help="Progress/completion notes")
@click.option("--claim", is_flag=True, help="Atomically claim the jack")
def update(jack_id, status, notes, claim):
    """Update a jack's status, notes, or claim it.

    Thin wrapper around `bd update` that also stamps the current session ID
    onto the jack and logs the update to the session JSONL.

    JACK_ID: beads jack ID (e.g. jack-a1b2)
    """
    cmd = ["update", jack_id]

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

    console.print(f"[green]Updated {jack_id}.[/green]")
    if result.stdout.strip():
        console.print(result.stdout.strip())

    # Log to session JSONL
    config = _try_load_config()
    sessions_log = config.sessions_log_path if config else SESSIONS_LOG
    _log_update_event(sessions_log, jack_id, status, notes, claim)


def _log_update_event(sessions_log: Path, jack_id: str, status, notes, claim):
    """Append an update event to the sessions JSONL."""
    sessions_log.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "event": "jack_update",
        "jack_id": jack_id,
        "status": status,
        "notes": notes,
        "claimed": claim,
        "actor": os.environ.get("USER", "unknown"),
        "session_id": os.environ.get("CLAUDE_SESSION_ID"),
        "timestamp": datetime.datetime.now().isoformat(),
    }
    with open(sessions_log, "a") as f:
        f.write(json.dumps(event) + "\n")


# ── sw done ──────────────────────────────────────────────────────────────────

@main.command()
@click.argument("jack_id")
@click.option("--commit-msg", "-m", default=None, help="Commit message for this jack")
@click.option("--diff-summary", "-d", default=None, help="Brief diff/change summary")
def done(jack_id, commit_msg, diff_summary):
    """Mark a jack done and index its completion context into Qdrant.

    Closes the jack via bd, then indexes the commit message and diff summary
    for semantic search. Patch cord (dependency) unblocking is handled by bd.

    JACK_ID: beads jack ID (e.g. jack-a1b2)
    """
    result = _run_bd("close", jack_id)
    if result.returncode != 0:
        console.print(f"[red]Error:[/red] {result.stderr.strip()}")
        raise SystemExit(1)
    console.print(f"[green]Jack {jack_id} closed.[/green]")

    # Index completion context in Qdrant if available
    if commit_msg or diff_summary:
        config = _try_load_config()
        if config:
            host, port, collection = config.qdrant.host, config.qdrant.port, config.qdrant.collection
        else:
            host, port, collection = "localhost", 6333, "switchyard"
        try:
            from .qdrant import get_client, index_jack_completion, ensure_collection
            client = get_client(host=host, port=port)
            ensure_collection(client, collection)
            index_jack_completion(
                client, collection, jack_id,
                commit_msg=commit_msg or "",
                diff_summary=diff_summary or "",
            )
            console.print(f"[dim]Indexed completion context in Qdrant.[/dim]")
        except Exception as e:
            console.print(f"[dim]Qdrant unavailable, skipping index: {e}[/dim]")


# ── sw search ────────────────────────────────────────────────────────────────

@main.command()
@click.argument("query")
@click.option("--limit", "-l", default=10, help="Number of results")
@click.option("--repo", "-r", default=None, help="Filter by repo ID")
def search(query, limit, repo):
    """Search jacks by text or semantic query.

    Tries Qdrant semantic search first, falls back to `bd search` for
    text-based search over jack titles and IDs.

    QUERY: search query string
    """
    # Try Qdrant semantic search first
    config = _try_load_config()
    if config:
        host, port, collection = config.qdrant.host, config.qdrant.port, config.qdrant.collection
    else:
        host, port, collection = "localhost", 6333, "switchyard"
    try:
        from .qdrant import get_client, search as qdrant_search
        client = get_client(host=host, port=port)
        hits = qdrant_search(client, collection, query, limit=limit, repo_filter=repo)
        if hits:
            table = Table(title=f'Search (semantic): "{query}"', box=box.ROUNDED, show_header=True, header_style="bold")
            table.add_column("ID", style="cyan")
            table.add_column("Score", justify="right", style="dim")
            table.add_column("Status")
            table.add_column("Title")
            for h in hits:
                st = h.get("status", "?")
                color = _status_color(st)
                table.add_row(
                    h.get("jack_id", "?"),
                    f"{h.get('score', 0):.2f}",
                    f"[{color}]{st}[/{color}]",
                    h.get("title", ""),
                )
            console.print(table)
            return
    except Exception:
        pass  # Fall through to text search

    # Text search via bd search
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

    jacks = json.loads(result.stdout) if result.stdout.strip() else []
    if not jacks:
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

    for j in jacks:
        st = j.get("status", "?")
        color = _status_color(st)
        table.add_row(
            j.get("id", "?"),
            f"[{color}]{st}[/{color}]",
            str(j.get("priority", "?")),
            j.get("title", ""),
        )
    console.print(table)


# ── sw tree ──────────────────────────────────────────────────────────────────

@main.command()
@click.option("--repo", "-r", default=None, help="Filter by repo ID")
@click.option("--depth", "-d", default=None, type=int, help="Max tree depth")
def tree(repo, depth):
    """Render the patch cord (dependency) tree with status colours.

    Fetches the full jack graph from beads and renders it as an ASCII tree
    using rich, colour-coded by status (open=white, claimed=blue,
    blocked=yellow, hold=magenta, done=green).

    Shows cross-repo `triggers_update` relations with a distinct marker.
    """
    # Get all jacks (including closed to show full tree)
    cmd = ["list", "--flat", "--limit", "0"]
    if repo:
        cmd.extend(["--label", repo])
    jacks = _run_bd_json(*cmd)

    if not jacks:
        console.print("[dim]No jacks found.[/dim]")
        return

    # Build lookup by ID
    jack_map = {j["id"]: j for j in jacks}

    # Get patch cord (dependency) info for each jack
    dep_graph = {}   # jack_id -> list of dependency IDs (what it depends on)
    child_graph = {}  # jack_id -> list of jack IDs that depend on it

    all_ids = list(jack_map.keys())
    for jid in all_ids:
        dep_graph[jid] = []
        child_graph[jid] = []

    # Fetch deps for all jacks
    if all_ids:
        dep_result = _run_bd("dep", "list", *all_ids, "--json")
        if dep_result.returncode == 0 and dep_result.stdout.strip():
            deps = json.loads(dep_result.stdout)
            for d in deps:
                # dep record: from_id depends on to_id (patch cord)
                from_id = d.get("from_id") or d.get("issue_id", "")
                to_id = d.get("to_id") or d.get("depends_on", "")
                if from_id and to_id:
                    dep_graph.setdefault(from_id, []).append(to_id)
                    child_graph.setdefault(to_id, []).append(from_id)

    # Find root jacks (no patch cords in, or deps are all outside our set)
    roots = [
        jid for jid in all_ids
        if not dep_graph.get(jid)
    ]

    # If no clear roots, show all jacks as roots (flat fallback)
    if not roots:
        roots = all_ids

    # Render with rich.tree
    rich_tree = Tree("[bold]Switchboard Jack Graph[/bold]")
    rendered = set()

    def _add_node(parent_tree: Tree, jid: str, current_depth: int):
        if depth is not None and current_depth > depth:
            return
        if jid in rendered:
            # Show reference to already-rendered node
            j = jack_map.get(jid, {})
            parent_tree.add(f"[dim]-> {jid}[/dim] [dim](see above)[/dim]")
            return
        rendered.add(jid)

        j = jack_map.get(jid, {})
        st = j.get("status", "?")
        prio = j.get("priority", "?")
        title = j.get("title", "")
        itype = j.get("issue_type", "")
        color = _status_color(st)

        # Format: [ID] P# type: title (status)
        label = f"[{color}]{jid}[/{color}]  P{prio}  "
        if itype:
            label += f"[dim]{itype}:[/dim] "
        label += f"{title}  [{color}]({st})[/{color}]"

        node = parent_tree.add(label)

        # Recurse into children (jacks that depend on this one via patch cords)
        children = child_graph.get(jid, [])
        for child_id in children:
            if child_id in jack_map:
                _add_node(node, child_id, current_depth + 1)

    for root_id in roots:
        _add_node(rich_tree, root_id, 0)

    console.print(rich_tree)


# ── sw resume ────────────────────────────────────────────────────────────────

@main.command()
@click.argument("jack_id")
@click.option("--raw", is_flag=True, help="Print raw YAML instead of formatted view")
def resume(jack_id, raw):
    """Re-enter a jack mid-stream with a structured context orientation.

    Reads the jack's TSO (Task State Object) and presents a compact summary
    designed to position an operator correctly for continuation — not just
    inform, but orient.

    The TSO captures: goal, live assumptions, competing hypotheses,
    dead ends (with why), surprises, next action, and uncertainty profile.

    JACK_ID: jack identifier (used to locate state YAML)
    """
    tso = load_tso(jack_id)

    if raw:
        console.print(yaml.dump(tso, default_flow_style=False))
        return

    if tso.get("updated_at"):
        age = tso["updated_at"]
        header = f"[bold]Jack:[/bold] {jack_id}  [dim](state last updated: {age})[/dim]"
    else:
        header = f"[bold]Jack:[/bold] {jack_id}  [dim](no saved state — starting fresh)[/dim]"

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
    """Manage a jack's TSO (Jack State Object)."""
    pass


@state.command("set")
@click.argument("jack_id")
@click.option("--goal", default=None, help="Set the jack goal")
@click.option("--next", "next_action", default=None, help="Next action (format: 'action | why')")
@click.option("--uncertainty", default=None, help="Current uncertainty / open question")
@click.option("--hypothesis", "hypotheses", multiple=True, help="Add a hypothesis (repeatable)")
@click.option("--assumption", "assumptions", multiple=True, help="Add an assumption (repeatable)")
@click.option("--dead-end", "dead_ends", multiple=True, help="Add a dead end: 'what | why' (repeatable)")
@click.option("--surprise", "surprises", multiple=True, help="Add a surprise (repeatable)")
@click.option("--replace", is_flag=True, help="Replace lists instead of appending")
def state_set(jack_id, goal, next_action, uncertainty, hypotheses, assumptions, dead_ends, surprises, replace):
    """Update a jack's TSO fields.

    By default, hypothesis/assumption/dead-end/surprise options APPEND to
    existing lists. Use --replace to overwrite them instead.

    JACK_ID: jack identifier
    """
    tso = load_tso(jack_id)

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

    path = save_tso(jack_id, tso)
    console.print(f"[green]✓[/green] State saved: {path}")


@state.command("show")
@click.argument("jack_id")
def state_show(jack_id):
    """Show the raw TSO YAML for a jack.

    JACK_ID: jack identifier
    """
    tso = load_tso(jack_id)
    console.print(yaml.dump(tso, default_flow_style=False))


@state.command("clear")
@click.argument("jack_id")
@click.confirmation_option(prompt="Clear all TSO state for this jack?")
def state_clear(jack_id):
    """Delete the TSO for a jack.

    JACK_ID: jack identifier
    """
    path = get_tso_path(jack_id)
    if path.exists():
        path.unlink()
        console.print(f"[green]✓[/green] Cleared state for {jack_id}")
    else:
        console.print(f"[dim]No state found for {jack_id}[/dim]")


# ── sw mode ──────────────────────────────────────────────────────────────────

@main.group()
def mode():
    """Show or change workspace mode."""
    pass


@mode.command("set")
@click.argument("mode_value", metavar="MODE")
def mode_set(mode_value):
    """Set workspace mode: prototype or deliberate.

    prototype — holds with requires:any auto-ack (skip blocking); only requires:kale still blocks
    deliberate — all holds require explicit ack (default, safe)

    Saves to repos.yaml in cwd.
    """
    if mode_value not in ("prototype", "deliberate"):
        console.print(f"[red]Invalid mode:[/red] {mode_value}. Use 'prototype' or 'deliberate'.")
        raise SystemExit(1)
    repos_yaml = Path("repos.yaml")
    if not repos_yaml.exists():
        console.print("[red]repos.yaml not found in cwd[/red]")
        raise SystemExit(1)
    with open(repos_yaml) as f:
        raw = yaml.safe_load(f) or {}
    raw["mode"] = mode_value
    with open(repos_yaml, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)
    console.print(f"[green]Mode set to:[/green] [bold]{mode_value}[/bold]")
    if mode_value == "prototype":
        console.print("[dim]  holds with requires:any will auto-ack[/dim]")
    else:
        console.print("[dim]  all holds require explicit ack[/dim]")


@mode.command("show")
def mode_show():
    """Show current workspace mode and what it means."""
    config = _try_load_config()
    current = config.mode if config else "deliberate"
    console.print(f"[bold]Mode:[/bold] {current}")
    if current == "prototype":
        console.print("  [yellow]prototype[/yellow] — holds with requires:any auto-ack; requires:kale still blocks")
    else:
        console.print("  [green]deliberate[/green] — all holds require explicit ack (safe default)")


# ── sw session ────────────────────────────────────────────────────────────────

@main.group()
def session():
    """Manage session lifecycle events."""
    pass


@session.command("start")
@click.option("--actor", "-a", default=None, help="Operator starting the session (default: current user)")
def session_start(actor):
    """Start a new session, logging the event to the sessions JSONL."""
    if actor is None:
        actor = os.environ.get("USER", "unknown")

    config = _try_load_config()
    sessions_log = config.sessions_log_path if config else SESSIONS_LOG
    sessions_log.parent.mkdir(parents=True, exist_ok=True)

    event = {
        "event": "session_start",
        "actor": actor,
        "session_id": os.environ.get("CLAUDE_SESSION_ID"),
        "timestamp": datetime.datetime.now().isoformat(),
    }
    with open(sessions_log, "a") as f:
        f.write(json.dumps(event) + "\n")

    console.print(f"[green]Session started[/green] by [bold]{actor}[/bold]")
    console.print(f"  Logged to {sessions_log}")


@session.command("end")
@click.option("--notes", "-n", default=None, help="Session closing notes / summary")
def session_end(notes):
    """End the current session, logging the event with optional notes."""
    actor = os.environ.get("USER", "unknown")

    config = _try_load_config()
    sessions_log = config.sessions_log_path if config else SESSIONS_LOG
    sessions_log.parent.mkdir(parents=True, exist_ok=True)

    event = {
        "event": "session_end",
        "actor": actor,
        "session_id": os.environ.get("CLAUDE_SESSION_ID"),
        "notes": notes,
        "timestamp": datetime.datetime.now().isoformat(),
    }
    with open(sessions_log, "a") as f:
        f.write(json.dumps(event) + "\n")

    console.print(f"[green]Session ended[/green] by [bold]{actor}[/bold]")
    if notes:
        console.print(f"  Notes: {notes}")
    console.print(f"  Logged to {sessions_log}")


# ── sw reindex ────────────────────────────────────────────────────────────────

@main.command()
def reindex():
    """Rebuild the Qdrant search index from the full beads jack graph.

    Requires a running Qdrant instance and an embedding backend (openai or
    sentence-transformers). Gracefully errors if not configured.
    """
    config = _try_load_config()
    if config:
        host = config.qdrant.host
        port = config.qdrant.port
        collection = config.qdrant.collection
    else:
        host, port, collection = "localhost", 6333, "switchyard"

    try:
        from .qdrant import get_client, reindex_all
    except ImportError as e:
        console.print(f"[red]Qdrant not available:[/red] {e}")
        raise SystemExit(1)

    try:
        client = get_client(host=host, port=port)
        count = reindex_all(client, collection)
        console.print(f"[green]Indexed {count} jacks[/green] into [bold]{collection}[/bold]")
    except ImportError as e:
        console.print(f"[red]Embedding backend not available:[/red] {e}")
        console.print("[dim]Install openai or sentence-transformers to enable search indexing.[/dim]")
        raise SystemExit(1)
    except Exception as e:
        console.print(f"[red]Reindex failed:[/red] {e}")
        console.print("[dim]Is Qdrant running at {host}:{port}?[/dim]")
        raise SystemExit(1)
