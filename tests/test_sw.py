"""Tests for switchboard: config, TSO, checkpoints, and sessions."""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest
import yaml
from click.testing import CliRunner

from switchboard.config import load_config, Config, RepoConfig
from switchboard.cli import main, load_tso, save_tso, TSO_DIR, SESSIONS_LOG


# ── Config loading ────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_repos_yaml(tmp_path):
    """Create a temporary repos.yaml and return its path."""
    config = {
        "repos": [
            {
                "id": "test-repo",
                "name": "Test Repo",
                "remote": "git@github.com:example/test.git",
                "local_path": str(tmp_path / "test-repo"),
                "version": "1.0.0",
            },
            {
                "id": "other-repo",
                "name": "Other Repo",
                "remote": "git@github.com:example/other.git",
            },
        ],
        "checkpoint_defaults": {
            "requires": "kale",
            "test_requires": "any",
        },
        "qdrant": {
            "host": "localhost",
            "port": 6333,
            "collection": "test-collection",
        },
        "sessions": {
            "log_path": str(tmp_path / "sessions.jsonl"),
        },
    }
    path = tmp_path / "repos.yaml"
    path.write_text(yaml.dump(config))
    return path


def test_load_config_parses_repos(tmp_repos_yaml):
    cfg = load_config(tmp_repos_yaml)
    assert len(cfg.repos) == 2
    assert cfg.repos[0].id == "test-repo"
    assert cfg.repos[0].version == "1.0.0"
    assert cfg.repos[1].id == "other-repo"
    assert cfg.repos[1].version is None


def test_load_config_checkpoint_defaults(tmp_repos_yaml):
    cfg = load_config(tmp_repos_yaml)
    assert cfg.checkpoint_defaults.requires == "kale"
    assert cfg.checkpoint_defaults.test_requires == "any"


def test_load_config_qdrant_settings(tmp_repos_yaml):
    cfg = load_config(tmp_repos_yaml)
    assert cfg.qdrant.host == "localhost"
    assert cfg.qdrant.collection == "test-collection"


def test_load_config_get_repo(tmp_repos_yaml):
    cfg = load_config(tmp_repos_yaml)
    assert cfg.get_repo("test-repo") is not None
    assert cfg.get_repo("nonexistent") is None


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/repos.yaml"))


def test_load_config_duplicate_ids(tmp_path):
    config = {
        "repos": [
            {"id": "dupe", "name": "A", "remote": "x"},
            {"id": "dupe", "name": "B", "remote": "y"},
        ]
    }
    path = tmp_path / "repos.yaml"
    path.write_text(yaml.dump(config))
    with pytest.raises(ValueError, match="Duplicate repo IDs"):
        load_config(path)


# ── TSO save/load roundtrip ──────────────────────────────────────────────────

def test_tso_save_load_roundtrip(tmp_path):
    """TSO should survive a save/load cycle with all fields intact."""
    with mock.patch("switchboard.cli.TSO_DIR", tmp_path):
        tso = load_tso("test-task-1")
        assert tso["task_id"] == "test-task-1"
        assert tso["goal"] is None

        tso["goal"] = "Fix the widget parser"
        tso["assumptions"] = ["input is UTF-8", "max 10MB"]
        tso["hypotheses"] = ["off-by-one in line counter"]
        tso["dead_ends"] = [{"what": "regex approach", "why": "too slow"}]
        tso["next_action"] = {"action": "add unit test", "why": "reproduce first"}

        path = save_tso("test-task-1", tso)
        assert path.exists()

        loaded = load_tso("test-task-1")
        assert loaded["goal"] == "Fix the widget parser"
        assert loaded["assumptions"] == ["input is UTF-8", "max 10MB"]
        assert loaded["hypotheses"] == ["off-by-one in line counter"]
        assert loaded["dead_ends"] == [{"what": "regex approach", "why": "too slow"}]
        assert loaded["next_action"]["action"] == "add unit test"
        assert loaded["updated_at"] is not None


# ── Checkpoint listing with mocked bd ─────────────────────────────────────────

def test_checkpoint_list_with_mocked_bd():
    """sw checkpoint list should parse bd JSON output."""
    mock_tasks = [
        {
            "id": "bd-aaaa",
            "title": "Review API design",
            "issue_type": "checkpoint",
            "status": "open",
            "assignee": "kale",
            "description": "Review the API before proceeding",
            "labels": [],
        },
        {
            "id": "bd-bbbb",
            "title": "Regular task",
            "issue_type": "task",
            "status": "open",
            "assignee": "cleo",
            "description": "",
            "labels": [],
        },
    ]

    mock_result = mock.Mock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(mock_tasks)

    with mock.patch("switchboard.checkpoint._run_bd", return_value=mock_result):
        from switchboard.checkpoint import list_open_checkpoints
        checkpoints = list_open_checkpoints()
        assert len(checkpoints) == 1
        assert checkpoints[0].id == "bd-aaaa"
        assert checkpoints[0].title == "Review API design"


# ── Session start/end logging ─────────────────────────────────────────────────

def test_session_start_logs_event(tmp_path):
    """sw session start should append a session_start event to the JSONL log."""
    log_path = tmp_path / "sessions.jsonl"
    runner = CliRunner()

    with mock.patch("switchboard.cli._try_load_config") as mock_cfg:
        cfg = mock.Mock()
        cfg.sessions_log_path = log_path
        mock_cfg.return_value = cfg

        result = runner.invoke(main, ["session", "start", "--actor", "testuser"])
        assert result.exit_code == 0
        assert "Session started" in result.output

    events = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["event"] == "session_start"
    assert events[0]["actor"] == "testuser"


def test_session_end_logs_event(tmp_path):
    """sw session end should append a session_end event with notes."""
    log_path = tmp_path / "sessions.jsonl"
    runner = CliRunner()

    with mock.patch("switchboard.cli._try_load_config") as mock_cfg:
        cfg = mock.Mock()
        cfg.sessions_log_path = log_path
        mock_cfg.return_value = cfg

        result = runner.invoke(main, ["session", "end", "--notes", "All done"])
        assert result.exit_code == 0
        assert "Session ended" in result.output

    events = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["event"] == "session_end"
    assert events[0]["notes"] == "All done"


def test_session_start_default_actor(tmp_path):
    """sw session start without --actor should use $USER."""
    log_path = tmp_path / "sessions.jsonl"
    runner = CliRunner()

    with mock.patch("switchboard.cli._try_load_config") as mock_cfg:
        cfg = mock.Mock()
        cfg.sessions_log_path = log_path
        mock_cfg.return_value = cfg

        with mock.patch.dict(os.environ, {"USER": "whoami"}):
            result = runner.invoke(main, ["session", "start"])
            assert result.exit_code == 0

    events = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert events[0]["actor"] == "whoami"
