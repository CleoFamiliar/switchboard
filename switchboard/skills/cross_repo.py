"""CrossRepoSkill — detect and manage cross-repo dependencies.

Detection pipeline (in order, stop at first match):
1. Explicit tag (HIGH): label matching 'needs:<repo-id>/<artifact>'
2. Convention match (HIGH): title/description contains '[repo-id]' or 'requires <repo-id>'
3. Known artifact registry (MEDIUM): description mentions a registered artifact name
4. LLM inference (LOW): call 'claude --print' to infer cross-repo dependencies
"""

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

from .base import BaseSkill, Confidence, SkillResult

logger = logging.getLogger(__name__)

# Events this skill handles
_HANDLED_EVENTS = {"task.created", "task.closed", "pr.opened", "pr.merged"}


@dataclass
class DetectedDependency:
    from_repo: str
    to_repo: str
    artifact: Optional[str]
    confidence: Confidence


class CrossRepoSkill(BaseSkill):
    name = "cross_repo"
    description = "Detect and manage cross-repo dependencies"

    def should_run(self, event: dict) -> bool:
        return event.get("type") in _HANDLED_EVENTS

    def run(self, event: dict, config) -> list[SkillResult]:
        from ..notifications import append_notification

        repo_ids = [r.id for r in config.repos]
        jack_id = event.get("jack_id", "")
        title = event.get("title", "")
        body = event.get("body", "")
        labels = event.get("labels", [])
        event_type = event.get("type", "")

        deps = detect_dependencies(
            title=title,
            body=body,
            labels=labels,
            repo_ids=repo_ids,
            artifacts_by_repo=_build_artifact_index(config),
        )

        if not deps:
            return []

        results: list[SkillResult] = []

        for dep in deps:
            # Create bd dep link
            _create_dep_link(jack_id, dep)

            if event_type == "task.closed" and dep.confidence == Confidence.HIGH:
                # Auto-queue unblocked downstream tasks
                unblocked = _auto_queue_downstream(jack_id, dep)
                result = SkillResult(
                    skill=self.name,
                    confidence=dep.confidence,
                    action=f"Auto-queued {len(unblocked)} downstream task(s) in {dep.to_repo} "
                           f"(artifact: {dep.artifact or 'n/a'})",
                    jack_ids=[jack_id] + unblocked,
                    auto_applied=True,
                )
                results.append(result)
            else:
                # Notify operator for MEDIUM/LOW confidence or non-close events
                action = (
                    f"Cross-repo dependency detected: {dep.from_repo} -> {dep.to_repo} "
                    f"(artifact: {dep.artifact or 'n/a'}, confidence: {dep.confidence.value})"
                )
                result = SkillResult(
                    skill=self.name,
                    confidence=dep.confidence,
                    action=action,
                    jack_ids=[jack_id],
                    auto_applied=False,
                )
                results.append(result)

                # Write to notification queue for MEDIUM/LOW
                if dep.confidence in (Confidence.MEDIUM, Confidence.LOW):
                    try:
                        append_notification(
                            skill=self.name,
                            confidence=dep.confidence.value,
                            message=action,
                            jack_ids=[jack_id],
                        )
                    except Exception as e:
                        logger.warning("Failed to write notification: %s", e)

        return results


def detect_dependencies(
    title: str,
    body: str,
    labels: list[str],
    repo_ids: list[str],
    artifacts_by_repo: Optional[dict[str, list[str]]] = None,
) -> list[DetectedDependency]:
    """Run the detection pipeline. Returns detected dependencies."""

    # 1. Explicit tag: label matching 'needs:<repo-id>/<artifact>'
    deps = _check_explicit_tags(labels, repo_ids)
    if deps:
        return deps

    # 2. Convention match: '[repo-id]' or 'requires <repo-id>'
    deps = _check_convention_match(title, body, repo_ids)
    if deps:
        return deps

    # 3. Known artifact registry
    if artifacts_by_repo:
        deps = _check_artifact_registry(title, body, artifacts_by_repo)
        if deps:
            return deps

    # 4. LLM inference (best-effort)
    deps = _check_llm_inference(title, body, repo_ids)
    if deps:
        return deps

    return []


def _check_explicit_tags(labels: list[str], repo_ids: list[str]) -> list[DetectedDependency]:
    """Check for 'needs:<repo-id>/<artifact>' labels."""
    results = []
    needs_pattern = re.compile(r"^needs:([^/]+)/(.+)$")
    for label in labels:
        m = needs_pattern.match(label)
        if m:
            repo_id, artifact = m.group(1), m.group(2)
            if repo_id in repo_ids:
                results.append(DetectedDependency(
                    from_repo="",  # inferred from context
                    to_repo=repo_id,
                    artifact=artifact,
                    confidence=Confidence.HIGH,
                ))
    return results


def _check_convention_match(title: str, body: str, repo_ids: list[str]) -> list[DetectedDependency]:
    """Check for '[repo-id]' or 'requires <repo-id>' patterns."""
    text = f"{title} {body}".lower()
    results = []
    for repo_id in repo_ids:
        # [repo-id] pattern
        if f"[{repo_id}]".lower() in text:
            results.append(DetectedDependency(
                from_repo="",
                to_repo=repo_id,
                artifact=None,
                confidence=Confidence.HIGH,
            ))
        # 'requires repo-id' pattern
        elif re.search(rf"\brequires\s+{re.escape(repo_id)}\b", text):
            results.append(DetectedDependency(
                from_repo="",
                to_repo=repo_id,
                artifact=None,
                confidence=Confidence.HIGH,
            ))
    return results


def _check_artifact_registry(
    title: str, body: str, artifacts_by_repo: dict[str, list[str]]
) -> list[DetectedDependency]:
    """Check if title/body mentions a known artifact name."""
    text = f"{title} {body}"
    results = []
    for repo_id, artifact_names in artifacts_by_repo.items():
        for art_name in artifact_names:
            if re.search(rf"\b{re.escape(art_name)}\b", text):
                results.append(DetectedDependency(
                    from_repo="",
                    to_repo=repo_id,
                    artifact=art_name,
                    confidence=Confidence.MEDIUM,
                ))
    return results


def _check_llm_inference(
    title: str, body: str, repo_ids: list[str]
) -> list[DetectedDependency]:
    """Use 'claude --print' for LLM-based dependency inference. Best-effort."""
    if not title and not body:
        return []

    prompt = (
        f"Given these repos: {repo_ids}\n"
        f"And this task:\n"
        f"Title: {title}\n"
        f"Description: {body}\n\n"
        f"Is there a cross-repo dependency? "
        f"Return ONLY valid JSON (no markdown): "
        f'{{"has_dependency": bool, "from_repo": str, "to_repo": str, '
        f'"artifact": str, "reasoning": str}}'
    )

    try:
        result = subprocess.run(
            ["claude", "--print", "-p", prompt],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []

        # Parse JSON from output
        output = result.stdout.strip()
        # Try to extract JSON from possible markdown wrapping
        json_match = re.search(r"\{.*\}", output, re.DOTALL)
        if not json_match:
            return []

        data = json.loads(json_match.group())
        if data.get("has_dependency") and data.get("to_repo") in repo_ids:
            return [DetectedDependency(
                from_repo=data.get("from_repo", ""),
                to_repo=data["to_repo"],
                artifact=data.get("artifact"),
                confidence=Confidence.LOW,
            )]
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, OSError):
        pass  # claude CLI unavailable or failed — skip gracefully

    return []


def _build_artifact_index(config) -> dict[str, list[str]]:
    """Build repo_id -> [artifact_names] mapping from config."""
    index: dict[str, list[str]] = {}
    for repo in config.repos:
        if hasattr(repo, "artifacts") and repo.artifacts:
            index[repo.id] = [a.name for a in repo.artifacts]
    return index


def _create_dep_link(jack_id: str, dep: DetectedDependency) -> None:
    """Create a bd dep link for the detected dependency."""
    if not jack_id or not dep.to_repo:
        return
    try:
        subprocess.run(
            ["bd", "dep", "add", jack_id, dep.to_repo],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        logger.warning("Failed to create dep link for %s -> %s", jack_id, dep.to_repo)


def _auto_queue_downstream(jack_id: str, dep: DetectedDependency) -> list[str]:
    """Find and auto-queue downstream tasks blocked by the completed jack."""
    unblocked: list[str] = []
    try:
        result = subprocess.run(
            ["bd", "dep", "list", jack_id, "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return unblocked

        deps = json.loads(result.stdout) if result.stdout.strip() else []
        for d in deps:
            downstream_id = d.get("from_id") or d.get("issue_id", "")
            if downstream_id and downstream_id != jack_id:
                subprocess.run(
                    ["bd", "update", downstream_id, "--status", "open"],
                    capture_output=True, text=True, timeout=10,
                )
                unblocked.append(downstream_id)
                logger.info("Auto-queued %s (unblocked by %s)", downstream_id, jack_id)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    return unblocked
