"""PRReviewSkill — respond to PR review feedback through Switchboard.

On a pr.reviewed event:
- CHANGES_REQUESTED: spawns an isolated subagent to read the feedback and
  address it on the branch, then pushes. Notifies Cleo via Matrix when done.
- COMMENTED: notifies Cleo via Matrix so she can decide whether to act.
- APPROVED: no-op (merge is handled by PRMergeSkill).

Requires env:
  OPENCLAW_WEBHOOK_URL   — e.g. http://127.0.0.1:18789/hooks/wake
  OPENCLAW_WEBHOOK_TOKEN — bearer token for the wake endpoint
  MATRIX_ROOM_ID         — room to notify (e.g. !DuJLlAtvuDbbDKaQkr:matrix.org)
"""

import logging
import os
import json
import urllib.request

from .base import BaseSkill, Confidence, SkillResult

logger = logging.getLogger(__name__)


def _wake_openclaw(text: str) -> None:
    """Send a wake event to the OpenClaw main session."""
    url = os.environ.get("OPENCLAW_WEBHOOK_URL", "http://127.0.0.1:18789/hooks/wake")
    token = os.environ.get("OPENCLAW_WEBHOOK_TOKEN", "")
    payload = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            logger.info("OpenClaw wake sent: %s", resp.status)
    except Exception as e:
        logger.warning("Failed to wake OpenClaw: %s", e)


class PRReviewSkill(BaseSkill):
    name = "pr-review"
    description = "Handle PR review feedback — auto-fix on changes-requested, notify on comments."

    def should_run(self, event: dict) -> bool:
        return event.get("type") == "pr.reviewed"

    def run(self, event: dict, config) -> list[SkillResult]:
        state = event.get("review_state", "").upper()
        pr_title = event.get("title", "")
        pr_url = event.get("pr_url", "")
        repo = event.get("repo", "")
        ref = event.get("ref", "")
        reviewer = event.get("reviewer", "")
        review_body = event.get("review_body", "")

        if state == "APPROVED":
            return []  # PRMergeSkill handles post-merge; nothing to do on approve

        if state == "CHANGES_REQUESTED":
            text = (
                f"PR review — changes requested on {repo} #{pr_url}\n"
                f"Branch: {ref}\n"
                f"Reviewer: {reviewer}\n"
                f"Feedback: {review_body or '(see inline comments on PR)'}\n\n"
                f"Please read the full review feedback on the PR, address the changes "
                f"on branch {ref}, push, and reply to the reviewer."
            )
            _wake_openclaw(text)
            return [
                SkillResult(
                    skill=self.name,
                    confidence=Confidence.HIGH,
                    action=f"changes requested on {repo}/{ref} — woke Cleo to address",
                    jack_ids=[],
                    auto_applied=True,
                )
            ]

        if state in ("COMMENTED", "DISMISSED"):
            text = (
                f"PR review comment on {repo} #{pr_url}\n"
                f"Branch: {ref} | Reviewer: {reviewer}\n"
                f"{review_body or '(inline comment — no summary body)'}\n\n"
                f"Review and decide whether to act."
            )
            _wake_openclaw(text)
            return [
                SkillResult(
                    skill=self.name,
                    confidence=Confidence.MEDIUM,
                    action=f"review comment on {repo}/{ref} — notified Cleo",
                    jack_ids=[],
                    auto_applied=True,
                )
            ]

        return []
