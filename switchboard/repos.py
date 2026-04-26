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


def get_repo_status(repo_id: str) -> RepoStatus:
    """Return task graph status summary for a single repo.

    Queries beads for tasks tagged to this repo, counts by status, and
    identifies any pending triggers_update tasks pointing to it.
    """
    raise NotImplementedError("get_repo_status: not yet implemented")


def get_all_repo_statuses(repos: list[RepoConfig]) -> list[RepoStatus]:
    """Return status for all registered repos.

    Calls get_repo_status for each repo and returns the list. Used by
    `sw status` to render the workspace-wide dashboard.
    """
    raise NotImplementedError("get_all_repo_statuses: not yet implemented")


def find_triggers_update_tasks(from_repo_id: str, to_repo_id: Optional[str] = None) -> list[str]:
    """Find open tasks with triggers_update deps from one repo to another.

    Returns task IDs that are waiting for a cross-repo update. If to_repo_id
    is None, returns all triggers_update tasks from the given repo.
    """
    raise NotImplementedError("find_triggers_update_tasks: not yet implemented")
