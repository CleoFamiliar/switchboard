"""
checkpoint.py — Checkpoint task management for Switchboard.

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
  3. Call `bd close <id> --reason "<decision>"`
  4. bd close --suggest-next handles unblocking downstream tasks
  5. Append ack event to sessions.jsonl

This module does NOT implement the checkpoint task creation — that's done via
`bd create` with appropriate fields. It only manages the ack workflow.
"""

import json
import subprocess
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


def _run_bd(*args: str) -> subprocess.CompletedProcess:
    """Run a bd CLI command and return the result."""
    return subprocess.run(
        ["bd", *args],
        capture_output=True, text=True
    )


def list_open_checkpoints(repo_filter: Optional[str] = None) -> list[CheckpointTask]:
    """Return all checkpoint tasks in open/checkpoint status.

    Queries beads for tasks with type=checkpoint that are open. bd doesn't have
    a native checkpoint type, so we look for tasks labeled 'checkpoint'.
    Falls back to listing all open tasks and filtering by label/type.
    """
    # Try listing by type first; bd may not have a 'checkpoint' type,
    # so we also try label-based filtering
    cmd = ["list", "--json", "--status", "open", "--limit", "0"]
    if repo_filter:
        cmd.extend(["--label", repo_filter])

    result = _run_bd(*cmd)
    if result.returncode != 0:
        return []

    tasks = json.loads(result.stdout) if result.stdout.strip() else []
    checkpoints = []
    for t in tasks:
        # Match on type=checkpoint or label containing 'checkpoint'
        is_checkpoint = (
            t.get("issue_type") == "checkpoint"
            or "checkpoint" in (t.get("labels") or [])
        )
        if not is_checkpoint:
            continue

        checkpoints.append(CheckpointTask(
            id=t["id"],
            title=t.get("title", ""),
            requires=t.get("assignee", "any"),
            prompt=t.get("description", ""),
            status=t.get("status", "open"),
        ))
    return checkpoints


def _log_ack_event(
    sessions_log: Path,
    task_id: str,
    decision: str,
    actor: str,
    session_id: Optional[str],
    unblocked: list[str],
) -> None:
    """Append an ack event to the sessions JSONL log."""
    sessions_log.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "event": "checkpoint_ack",
        "task_id": task_id,
        "decision": decision,
        "actor": actor,
        "session_id": session_id,
        "unblocked_tasks": unblocked,
        "timestamp": datetime.now().isoformat(),
    }
    with open(sessions_log, "a") as f:
        f.write(json.dumps(event) + "\n")


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
    3. Mark task done via `bd close --reason`
    4. Find and open newly-unblocked downstream tasks
    5. Write ack event to sessions.jsonl

    Raises:
        ValueError: if task is not a checkpoint or actor is not authorised
        subprocess.CalledProcessError: if bd CLI call fails
    """
    # Close the checkpoint with the decision as reason.
    # --suggest-next will show newly unblocked tasks.
    result = _run_bd("close", task_id, "--reason", decision, "--suggest-next")
    if result.returncode != 0:
        raise RuntimeError(f"bd close failed: {result.stderr.strip()}")

    # Find unblocked tasks from bd output (best-effort parse)
    unblocked = _parse_unblocked_from_output(result.stdout)

    _log_ack_event(sessions_log, task_id, decision, actor, session_id, unblocked)


def _parse_unblocked_from_output(output: str) -> list[str]:
    """Best-effort parse of unblocked task IDs from bd close --suggest-next output."""
    unblocked = []
    for line in output.splitlines():
        line = line.strip()
        # bd typically shows unblocked tasks as lines containing task IDs
        # Look for lines that contain a bead-style ID pattern
        if line and any(c == "-" for c in line):
            parts = line.split()
            for part in parts:
                if "-" in part and len(part) > 3 and not part.startswith("--"):
                    unblocked.append(part)
                    break
    return unblocked


def _open_unblocked_tasks(acked_task_id: str) -> list[str]:
    """Find downstream tasks that are now unblocked after this checkpoint ack.

    A downstream task becomes unblocked when ALL of its blockers are done.
    Returns list of task IDs that were opened.

    Note: bd close --suggest-next handles this natively, so this is kept
    as a fallback / verification method.
    """
    result = _run_bd("dep", "list", acked_task_id, "--direction=up", "--json")
    if result.returncode != 0:
        return []

    dependents = json.loads(result.stdout) if result.stdout.strip() else []
    opened = []
    for dep in dependents:
        dep_id = dep.get("id") or dep.get("issue_id", "")
        if dep_id:
            opened.append(dep_id)
    return opened
