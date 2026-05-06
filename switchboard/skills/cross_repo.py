"""CrossRepoSkill — detect and manage cross-repo dependencies.

Detection pipeline (in order, stop at first match):
1. Explicit tag (HIGH): label matching 'needs:<repo-id>/<artifact>'
2. Convention match (HIGH): title/description contains '[repo-id]' or 'requires <repo-id>'
3. Known artifact registry (MEDIUM): description mentions a registered artifact name
4. LLM inference (LOW): call 'claude --print' to infer cross-repo dependencies
"""

import hashlib
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .base import BaseSkill, Confidence, SkillResult

logger = logging.getLogger(__name__)

# Events this skill handles
_HANDLED_EVENTS = {"task.created", "task.closed", "pr.opened", "pr.merged"}

# ── LLM result cache ─────────────────────────────────────────────────────────

_CACHE_PATH = Path.home() / '.switchboard' / 'llm-cache.json'
_CACHE_TTL = 86400  # 24 hours


def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, indent=2))


def _cache_key(title: str, body: str, repo_ids: list[str]) -> str:
    payload = json.dumps({'title': title, 'body': body, 'repos': sorted(repo_ids)}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


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


def _extract_json(text: str) -> Optional[dict]:
    """Robust JSON extraction — tries regex, then progressively smaller substrings."""
    # 1. Try regex for outermost { ... }
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    # 2. Try progressively smaller substrings starting from each '{'
    for i, ch in enumerate(text):
        if ch == '{':
            for j in range(len(text), i, -1):
                if text[j - 1] == '}':
                    try:
                        return json.loads(text[i:j])
                    except json.JSONDecodeError:
                        continue
    return None


def _check_llm_inference(
    title: str, body: str, repo_ids: list[str]
) -> list[DetectedDependency]:
    """Use 'claude --print' for LLM-based dependency inference. Best-effort."""
    if not title and not body:
        return []

    # Check BEADS_NO_LLM env var
    if os.environ.get('BEADS_NO_LLM'):
        return []

    # Check cache
    key = _cache_key(title, body, repo_ids)
    cache = _load_cache()
    cached = cache.get(key)
    if cached and (time.time() - cached.get('ts', 0)) < _CACHE_TTL:
        data = cached.get('result')
        if data and data.get("has_dependency") and data.get("to_repo") in repo_ids:
            return [DetectedDependency(
                from_repo=data.get("from_repo", ""),
                to_repo=data["to_repo"],
                artifact=data.get("artifact"),
                confidence=Confidence.LOW,
            )]
        return []

    prompt = (
        'You are analyzing a software task to detect cross-repository dependencies.\n\n'
        f'Registered repos: {repo_ids}\n\n'
        f'Task title: {title}\n'
        f'Task description: {body or "(none)"}\n\n'
        'Does this task require an artifact, component, API, or contract from a DIFFERENT repo listed above?\n'
        'Only answer yes if there is a clear, specific dependency — not just general similarity.\n\n'
        'Respond with ONLY valid JSON (no markdown fences):\n'
        '{"has_dependency": bool, "from_repo": "repo that needs the artifact (or empty)", '
        '"to_repo": "repo that provides the artifact", '
        '"artifact": "specific artifact name (or empty)", '
        '"reasoning": "one sentence"}'
    )

    try:
        result = subprocess.run(
            ["claude", "--print", "-p", prompt],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []

        # Parse JSON from output using robust extractor
        output = result.stdout.strip()
        data = _extract_json(output)
        if data is None:
            return []

        # Cache the result
        cache[key] = {'ts': time.time(), 'result': data}
        _save_cache(cache)

        if data.get("has_dependency") and data.get("to_repo") in repo_ids:
            return [DetectedDependency(
                from_repo=data.get("from_repo", ""),
                to_repo=data["to_repo"],
                artifact=data.get("artifact"),
                confidence=Confidence.LOW,
            )]
    except subprocess.TimeoutExpired:
        logger.warning("LLM inference timed out for jack: %s", title[:80])
    except (FileNotFoundError, json.JSONDecodeError, OSError):
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
