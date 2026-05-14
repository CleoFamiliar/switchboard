"""PRMergeSkill — close jacks when their associated PR is merged.

On a pr.merged event, parses the PR body for 'Closes <id>' / 'Fixes <id>'
references, closes matching jacks via bd, and logs a decision note.

Pattern matched (case-insensitive):
  Closes butiq-abc
  Fixes switchboard-xyz
  closes butiq-abc, butiq-def   (comma-separated on same line)

Auto-applied at HIGH confidence — merge is the natural close signal.
"""

import logging
import re
import subprocess
from pathlib import Path

from .base import BaseSkill, Confidence, SkillResult

logger = logging.getLogger(__name__)

# Matches e.g. "Closes butiq-abc" or "Fixes switchboard-xyz" or "closes butiq-abc, butiq-def"
_CLOSES_RE = re.compile(
    r'(?:closes?|fixes?)\s+([a-z][a-z0-9]*-[a-z0-9]+'
    r'(?:\s*,\s*[a-z][a-z0-9]*-[a-z0-9]+)*)',
    re.IGNORECASE,
)


def _extract_jack_ids(body: str) -> list[str]:
    """Return all jack IDs referenced in Closes/Fixes lines."""
    ids = []
    for match in _CLOSES_RE.finditer(body):
        for raw_id in re.split(r'\s*,\s*', match.group(1)):
            jack_id = raw_id.strip().lower()
            if jack_id:
                ids.append(jack_id)
    return ids


def _bd(*args, cwd: Path | None = None) -> tuple[int, str]:
    """Run a bd command, return (returncode, combined output)."""
    result = subprocess.run(
        ['bd', *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def _find_repo_dir(jack_id: str) -> Path | None:
    """Guess the repo directory from the jack prefix (e.g. 'butiq-abc' → ~/projects/butiq)."""
    prefix = jack_id.split('-')[0]
    candidates = [
        Path.home() / 'projects' / prefix,
        Path.home() / prefix,
    ]
    for c in candidates:
        if (c / '.beads').exists():
            return c
    return None


class PRMergeSkill(BaseSkill):
    name = "pr-merge"
    description = "Close jacks referenced in a merged PR body (Closes/Fixes <id>)."

    def should_run(self, event: dict) -> bool:
        return event.get("type") == "pr.merged"

    def run(self, event: dict, config) -> list[SkillResult]:
        body = event.get("body") or ""
        pr_title = event.get("title", "")
        pr_ref = event.get("ref", "")
        repo = event.get("repo", "")

        jack_ids = _extract_jack_ids(body)
        if not jack_ids:
            logger.debug("pr.merged: no Closes/Fixes references found in PR body")
            return []

        results = []
        closed = []
        failed = []

        for jack_id in jack_ids:
            cwd = _find_repo_dir(jack_id)
            note = f"merged: {repo}/{pr_ref} — {pr_title}"

            rc, out = _bd('close', jack_id, '-m', note, cwd=cwd)
            if rc == 0:
                closed.append(jack_id)
                logger.info("Closed jack %s on PR merge (%s)", jack_id, pr_ref)
            else:
                failed.append(jack_id)
                logger.warning("Failed to close jack %s: %s", jack_id, out)

        if closed:
            # Push state to remote
            for jack_id in closed:
                cwd = _find_repo_dir(jack_id)
                _bd('dolt', 'push', cwd=cwd)

        action_parts = []
        if closed:
            action_parts.append(f"closed: {', '.join(closed)}")
        if failed:
            action_parts.append(f"failed: {', '.join(failed)}")

        return [
            SkillResult(
                skill=self.name,
                confidence=Confidence.HIGH,
                action=" | ".join(action_parts) or "no jacks closed",
                jack_ids=closed,
                auto_applied=bool(closed),
            )
        ]
