"""
checkpoint.py — Checkpoint task management for Switchyard.

A checkpoint is a beads task with `type=checkpoint` that blocks all downstream
tasks until a human (or authorised agent) explicitly acks it with a decision.

This module handles:
- Listing open checkpoints (from beads task graph)
- Acking a checkpoint: marking done, recording decision, opening downstream tasks
- Checkpoint authority: who can ack (kale | cleo | any)
- Session logging: every ack is written to the session JSONL for audit trail

Checkpoint ack flow:
  1. Validate task exists and is of type checkpoint
  2. Validate actor is authorised (task.requires field)
  3. Call `bd update <id> --status done --notes "<decision>"`
  4. Find all tasks that have this checkpoint as a direct blocker
  5. For each: if all their blockers are now done, `bd update <dep_id> --status open`
  6. Append ack event to sessions.jsonl

This module does NOT implement the checkpoint task creation — that's done via
`bd create` with appropriate fields. It only manages the ack workflow.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class CheckpointTask:
    id: str
    title: str
    requires: str        # "kale" | "cleo" | "any"
    prompt: str          # what needs to be decided/reviewed
    decision: Optional[str] = None
    status: str = "checkpoint"


def list_open_checkpoints(repo_filter: Optional[str] = None) -> list[CheckpointTask]:
    """Return all checkpoint tasks in open/checkpoint status.

    Queries beads (`bd list --type checkpoint --status open`) and parses
    output into CheckpointTask objects. Optionally filters by repo tag.
    """
    raise NotImplementedError("list_open_checkpoints: not yet implemented")


def ack_checkpoint(
    task_id: str,
    decision: str,
    actor: str,
    session_id: Optional[str],
    sessions_log: Path,
) -> None:
    """Acknowledge a checkpoint, recording the decision and unblocking downstream.

    Steps:
    1. Load task from beads, validate it's a checkpoint in open/checkpoint state
    2. Validate actor is authorised per task.requires
    3. Mark task done via `bd update`
    4. Find and open newly-unblocked downstream tasks
    5. Write ack event to sessions.jsonl

    Raises:
        ValueError: if task is not a checkpoint or actor is not authorised
        subprocess.CalledProcessError: if bd CLI call fails
    """
    raise NotImplementedError("ack_checkpoint: not yet implemented")


def _open_unblocked_tasks(acked_task_id: str) -> list[str]:
    """Find downstream tasks that are now unblocked after this checkpoint ack.

    A downstream task becomes unblocked when ALL of its blockers are done.
    Returns list of task IDs that were opened.
    """
    raise NotImplementedError("_open_unblocked_tasks: not yet implemented")
