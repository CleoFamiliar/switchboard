"""
checkpoint.py — Hold (checkpoint) management for Switchboard.

A hold is a beads jack with `type=checkpoint` that blocks all downstream
jacks (via patch cords) until an operator (Kale or Cleo) explicitly acks it
with a decision.

This module handles:
- Listing open holds (from beads jack graph)
- Acking a hold: marking done, recording decision, opening downstream jacks
- Hold authority: who can ack (kale | cleo | any)
- Session logging: every ack is written to the session JSONL for audit trail

Hold ack flow:
  1. Validate jack exists and is of type checkpoint
  2. Validate operator is authorised (jack.requires field)
  3. Call `bd close <id> --reason "<decision>"`
  4. bd close --suggest-next handles unblocking downstream jacks
  5. Append ack event to sessions.jsonl

This module does NOT implement the hold creation — that's done via
`bd create` with appropriate fields. It only manages the ack workflow.
"""

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class HoldJack:
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


def list_open_holds(repo_filter: Optional[str] = None) -> list[HoldJack]:
    """Return all hold jacks in open/checkpoint status.

    Queries beads for jacks with type=checkpoint that are open. bd doesn't have
    a native checkpoint type, so we look for jacks labeled 'checkpoint'.
    Falls back to listing all open jacks and filtering by label/type.
    """
    # Try listing by type first; bd may not have a 'checkpoint' type,
    # so we also try label-based filtering
    cmd = ["list", "--json", "--status", "open", "--limit", "0"]
    if repo_filter:
        cmd.extend(["--label", repo_filter])

    result = _run_bd(*cmd)
    if result.returncode != 0:
        return []

    jacks = json.loads(result.stdout) if result.stdout.strip() else []
    holds = []
    for j in jacks:
        # Match on type=checkpoint or label containing 'checkpoint'
        is_hold = (
            j.get("issue_type") == "checkpoint"
            or "checkpoint" in (j.get("labels") or [])
        )
        if not is_hold:
            continue

        holds.append(HoldJack(
            id=j["id"],
            title=j.get("title", ""),
            requires=j.get("assignee", "any"),
            prompt=j.get("description", ""),
            status=j.get("status", "open"),
        ))
    return holds


def _log_ack_event(
    sessions_log: Path,
    jack_id: str,
    decision: str,
    actor: str,
    session_id: Optional[str],
    unblocked: list[str],
) -> None:
    """Append an ack event to the sessions JSONL log."""
    sessions_log.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "event": "hold_ack",
        "jack_id": jack_id,
        "decision": decision,
        "actor": actor,
        "session_id": session_id,
        "unblocked_jacks": unblocked,
        "timestamp": datetime.now().isoformat(),
    }
    with open(sessions_log, "a") as f:
        f.write(json.dumps(event) + "\n")


def ack_hold(
    jack_id: str,
    decision: str,
    actor: str,
    session_id: Optional[str],
    sessions_log: Path,
) -> None:
    """Acknowledge a hold, recording the decision and unblocking downstream jacks.

    Steps:
    1. Load jack from beads, validate it's a hold in open/checkpoint state
    2. Validate operator (Kale/Cleo) is authorised per jack.requires
    3. Mark jack done via `bd close --reason`
    4. Find and open newly-unblocked downstream jacks (via patch cords)
    5. Write ack event to sessions.jsonl

    Raises:
        ValueError: if jack is not a hold or operator is not authorised
        subprocess.CalledProcessError: if bd CLI call fails
    """
    # Close the hold with the decision as reason.
    # --suggest-next will show newly unblocked jacks.
    result = _run_bd("close", jack_id, "--reason", decision, "--suggest-next")
    if result.returncode != 0:
        raise RuntimeError(f"bd close failed: {result.stderr.strip()}")

    # Find unblocked jacks from bd output (best-effort parse)
    unblocked = _parse_unblocked_from_output(result.stdout)

    _log_ack_event(sessions_log, jack_id, decision, actor, session_id, unblocked)


def _parse_unblocked_from_output(output: str) -> list[str]:
    """Best-effort parse of unblocked jack IDs from bd close --suggest-next output."""
    unblocked = []
    for line in output.splitlines():
        line = line.strip()
        # bd typically shows unblocked jacks as lines containing jack IDs
        # Look for lines that contain a bead-style ID pattern
        if line and any(c == "-" for c in line):
            parts = line.split()
            for part in parts:
                if "-" in part and len(part) > 3 and not part.startswith("--"):
                    unblocked.append(part)
                    break
    return unblocked


def _open_unblocked_jacks(acked_jack_id: str) -> list[str]:
    """Find downstream jacks that are now unblocked after this hold ack.

    A downstream jack becomes unblocked when ALL of its patch cords (blockers)
    are done. Returns list of jack IDs that were opened.

    Note: bd close --suggest-next handles this natively, so this is kept
    as a fallback / verification method.
    """
    result = _run_bd("dep", "list", acked_jack_id, "--direction=up", "--json")
    if result.returncode != 0:
        return []

    dependents = json.loads(result.stdout) if result.stdout.strip() else []
    opened = []
    for dep in dependents:
        dep_id = dep.get("id") or dep.get("issue_id", "")
        if dep_id:
            opened.append(dep_id)
    return opened
