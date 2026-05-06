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
