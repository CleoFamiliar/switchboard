"""Tests for CrossRepoSkill detection pipeline.

Tests the four detection tiers:
1. Explicit tag (HIGH) — 'needs:<repo-id>/<artifact>' labels
2. Convention match (HIGH) — '[repo-id]' or 'requires <repo-id>'
3. Artifact registry (MEDIUM) — known artifact names from config
4. LLM inference (LOW) — claude --print subprocess (mocked)
"""

import json
from unittest import mock

import pytest

from switchboard.skills.cross_repo import (
    CrossRepoSkill,
    detect_dependencies,
    _check_explicit_tags,
    _check_convention_match,
    _check_artifact_registry,
    _check_llm_inference,
)
from switchboard.skills.base import Confidence
from switchboard.skills.registry import SkillRegistry
from switchboard.config import Config, RepoConfig, ArtifactConfig


def test_explicit_tag_detection():
    """Label 'needs:component-lib/Button' -> HIGH confidence."""
    deps = detect_dependencies(
        title="Update button styles",
        body="",
        labels=["needs:component-lib/Button"],
        repo_ids=["component-lib", "main-app"],
    )
    assert len(deps) == 1
    assert deps[0].to_repo == "component-lib"
    assert deps[0].artifact == "Button"
    assert deps[0].confidence == Confidence.HIGH


def test_convention_bracket_match():
    """Title '[component-lib] add button' -> HIGH confidence."""
    deps = detect_dependencies(
        title="[component-lib] add button",
        body="",
        labels=[],
        repo_ids=["component-lib", "main-app"],
    )
    assert len(deps) == 1
    assert deps[0].to_repo == "component-lib"
    assert deps[0].confidence == Confidence.HIGH


def test_convention_requires_match():
    """Body 'requires component-lib' -> HIGH confidence."""
    deps = detect_dependencies(
        title="Upgrade dependencies",
        body="This task requires component-lib to be updated first",
        labels=[],
        repo_ids=["component-lib", "main-app"],
    )
    assert len(deps) == 1
    assert deps[0].to_repo == "component-lib"
    assert deps[0].confidence == Confidence.HIGH


def test_artifact_registry_match():
    """Body mentions 'Button', artifact registered -> MEDIUM."""
    deps = detect_dependencies(
        title="Fix the Button hover state",
        body="The Button component needs a new hover style",
        labels=[],
        repo_ids=["component-lib", "main-app"],
        artifacts_by_repo={"component-lib": ["Button", "Modal"]},
    )
    assert len(deps) == 1
    assert deps[0].to_repo == "component-lib"
    assert deps[0].artifact == "Button"
    assert deps[0].confidence == Confidence.MEDIUM


def test_no_match_no_llm():
    """Empty jack, no LLM available -> empty results."""
    deps = detect_dependencies(
        title="",
        body="",
        labels=[],
        repo_ids=["component-lib", "main-app"],
    )
    assert deps == []


def test_full_pipeline_order():
    """Explicit tag found -> stops before artifact check."""
    deps = detect_dependencies(
        title="Fix Button hover state",
        body="The Button component needs updating",
        labels=["needs:component-lib/Button"],
        repo_ids=["component-lib", "main-app"],
        artifacts_by_repo={"component-lib": ["Button", "Modal"]},
    )
    # Should return only the explicit tag match (HIGH), not artifact match (MEDIUM)
    assert len(deps) == 1
    assert deps[0].confidence == Confidence.HIGH
    assert deps[0].artifact == "Button"


# ── Explicit tag edge cases ───────────────────────────────────────────────

def test_explicit_tag_multiple():
    deps = _check_explicit_tags(
        labels=["needs:component-lib/Button", "needs:main-app/Dashboard"],
        repo_ids=["component-lib", "main-app"],
    )
    assert len(deps) == 2


def test_explicit_tag_unknown_repo():
    deps = _check_explicit_tags(
        labels=["needs:unknown-repo/Widget"],
        repo_ids=["component-lib"],
    )
    assert len(deps) == 0


# ── Convention match edge cases ───────────────────────────────────────────

def test_convention_case_insensitive():
    deps = _check_convention_match(
        title="Fix [COMPONENT-LIB] button",
        body="",
        repo_ids=["component-lib"],
    )
    assert len(deps) == 1


def test_convention_no_match():
    deps = _check_convention_match(
        title="Fix a local bug",
        body="Nothing cross-repo",
        repo_ids=["component-lib", "main-app"],
    )
    assert len(deps) == 0


# ── LLM inference ─────────────────────────────────────────────────────────

def test_llm_inference_success():
    llm_response = json.dumps({
        "has_dependency": True,
        "from_repo": "main-app",
        "to_repo": "component-lib",
        "artifact": "Button",
        "reasoning": "mentions shared component",
    })
    mock_result = mock.Mock()
    mock_result.returncode = 0
    mock_result.stdout = llm_response

    with mock.patch("switchboard.skills.cross_repo.subprocess.run", return_value=mock_result):
        deps = _check_llm_inference("Refactor shared UI", "Update components", ["component-lib", "main-app"])
        assert len(deps) == 1
        assert deps[0].confidence == Confidence.LOW


def test_llm_inference_claude_not_found():
    with mock.patch(
        "switchboard.skills.cross_repo.subprocess.run",
        side_effect=FileNotFoundError("claude not found"),
    ):
        deps = _check_llm_inference("Task", "Description", ["component-lib"])
        assert len(deps) == 0


def test_llm_inference_no_dependency():
    llm_response = json.dumps({
        "has_dependency": False, "from_repo": "", "to_repo": "",
        "artifact": "", "reasoning": "no dep",
    })
    mock_result = mock.Mock()
    mock_result.returncode = 0
    mock_result.stdout = llm_response

    with mock.patch("switchboard.skills.cross_repo.subprocess.run", return_value=mock_result):
        deps = _check_llm_inference("Local fix", "Nothing cross-repo", ["component-lib"])
        assert len(deps) == 0


def test_llm_inference_empty_input():
    deps = _check_llm_inference("", "", ["component-lib"])
    assert len(deps) == 0


# ── Skill should_run ──────────────────────────────────────────────────────

def test_skill_should_run():
    skill = CrossRepoSkill()
    assert skill.should_run({"type": "task.created"})
    assert skill.should_run({"type": "task.closed"})
    assert skill.should_run({"type": "pr.opened"})
    assert skill.should_run({"type": "pr.merged"})
    assert not skill.should_run({"type": "comment.added"})
    assert not skill.should_run({"type": ""})
    assert not skill.should_run({})


# ── Registry ─────────────────────────────────────────────────────────────

def test_registry_dispatches(tmp_path):
    config = Config(
        repos=[
            RepoConfig(id="component-lib", name="component-lib", remote="x",
                       artifacts=[ArtifactConfig(name="Button", kind="component")]),
        ],
        sessions_log_path=tmp_path / "sessions.jsonl",
    )
    registry = SkillRegistry()
    registry.register(CrossRepoSkill())

    event = {
        "type": "task.created",
        "jack_id": "jack-002",
        "title": "Fix [component-lib] modal",
        "body": "",
        "labels": [],
    }

    with mock.patch("switchboard.skills.cross_repo._create_dep_link"), \
         mock.patch("switchboard.notifications.NOTIFICATIONS_PATH", tmp_path / "notif.jsonl"):
        results = registry.run_all(event, config)
        assert len(results) >= 1
        assert results[0].skill == "cross_repo"


# ── Notification integration ─────────────────────────────────────────────

def test_medium_confidence_writes_notification(tmp_path):
    from switchboard.notifications import list_notifications

    config = Config(
        repos=[
            RepoConfig(id="component-lib", name="component-lib", remote="x",
                       artifacts=[ArtifactConfig(name="Button", kind="component")]),
        ],
        sessions_log_path=tmp_path / "sessions.jsonl",
    )

    skill = CrossRepoSkill()
    event = {
        "type": "task.created",
        "jack_id": "jack-003",
        "title": "Update the Button styling",
        "body": "",
        "labels": [],
    }

    notif_path = tmp_path / "notifications.jsonl"
    with mock.patch("switchboard.skills.cross_repo._create_dep_link"), \
         mock.patch("switchboard.notifications.NOTIFICATIONS_PATH", notif_path):
        results = skill.run(event, config)

    assert any(r.confidence == Confidence.MEDIUM for r in results)
    notifications = list_notifications(path=notif_path)
    assert len(notifications) >= 1
    assert notifications[0]["status"] == "pending"
    assert notifications[0]["skill"] == "cross_repo"
