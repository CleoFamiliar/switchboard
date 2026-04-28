"""
repos.py — Repo registry and cross-repo dependency tracking.

Manages the set of repos registered in repos.yaml and tracks cross-repo
artifact dependencies modelled as `triggers_update` relations in beads.

Responsibilities:
- List registered repos and their current published versions
- Show which repos have pending `triggers_update` dependencies (i.e., a
  component was updated but downstream repos haven't been updated yet)
- Build the cross-repo status summary used by `sw status` and `sw tree`
- Version tracking: when a repo's published version changes, identify
  all downstream repos that have tasks tagged with a `triggers_update` dep

Cross-repo dependency model:
  A `triggers_update` relation on a beads task means:
  "when the parent task completes, work is required in a downstream repo."
  Example: component-lib publishes v2.1 → main-app update-dep task opens.

This module does NOT modify beads tasks directly — it reads the graph and
surfaces what's pending. Acking checkpoints is in checkpoint.py.
"""

import json
import subprocess
from dataclasses import dataclass
from typing import Optional

from .config import RepoConfig


@dataclass
class RepoStatus:
    repo: RepoConfig
    open_tasks: int
    blocked_tasks: int
    pending_checkpoints: int
    pending_updates: list[str]   # task IDs with triggers_update pointing here


def _run_bd(*args: str) -> subprocess.CompletedProcess:
    """Run a bd CLI command and return the result."""
    return subprocess.run(
        ["bd", *args],
        capture_output=True, text=True,
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


def get_repo_status(repo_id: str) -> RepoStatus:
    """Return task graph status summary for a single repo.

    Queries beads for tasks tagged to this repo, counts by status, and
    identifies any pending triggers_update tasks pointing to it.
    """
    from .config import load_config
    config = load_config()
    repo = config.get_repo(repo_id)
    if repo is None:
        raise ValueError(f"Unknown repo: {repo_id}")

    tasks = _run_bd_json("list", "--label", repo_id, "--status=open", "--limit", "0")
    if not isinstance(tasks, list):
        tasks = []

    open_count = 0
    blocked_count = 0
    checkpoint_count = 0

    for t in tasks:
        status = t.get("status", "open")
        if status == "blocked":
            blocked_count += 1
        else:
            open_count += 1
        if t.get("issue_type") == "checkpoint" or "checkpoint" in (t.get("labels") or []):
            checkpoint_count += 1

    pending_updates = find_triggers_update_tasks(from_repo_id=None, to_repo_id=repo_id)

    return RepoStatus(
        repo=repo,
        open_tasks=open_count,
        blocked_tasks=blocked_count,
        pending_checkpoints=checkpoint_count,
        pending_updates=pending_updates,
    )


def get_all_repo_statuses(repos: list[RepoConfig]) -> list[RepoStatus]:
    """Return status for all registered repos.

    Calls get_repo_status for each repo and returns the list. Used by
    `sw status` to render the workspace-wide dashboard.
    """
    return [get_repo_status(r.id) for r in repos]


def find_triggers_update_tasks(from_repo_id: Optional[str] = None, to_repo_id: Optional[str] = None) -> list[str]:
    """Find open tasks with triggers_update deps from one repo to another.

    Returns task IDs that are waiting for a cross-repo update. If to_repo_id
    is None, returns all triggers_update tasks from the given repo.

    Looks for tasks labeled 'triggers_update' (and optionally the repo labels)
    in the beads task graph.
    """
    cmd = ["list", "--status=open", "--limit", "0"]
    if from_repo_id:
        cmd.extend(["--label", from_repo_id])

    tasks = _run_bd_json(*cmd)
    if not isinstance(tasks, list):
        return []

    result_ids = []
    for t in tasks:
        labels = t.get("labels") or []
        title = (t.get("title") or "").lower()
        desc = (t.get("description") or "").lower()

        is_trigger = (
            "triggers_update" in labels
            or "triggers_update" in title
            or "triggers_update" in desc
        )
        if not is_trigger:
            continue

        if to_repo_id:
            if to_repo_id not in labels and to_repo_id not in title and to_repo_id not in desc:
                continue

        result_ids.append(t.get("id", ""))

    return [tid for tid in result_ids if tid]
