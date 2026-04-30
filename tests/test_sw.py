"""Tests for switchboard: config, TSO (jack state), holds, and sessions."""

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
        "mode": "deliberate",
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


def test_load_config_mode(tmp_repos_yaml):
    cfg = load_config(tmp_repos_yaml)
    assert cfg.mode == "deliberate"


def test_load_config_mode_default(tmp_path):
    """Config without mode field should default to deliberate."""
    config = {
        "repos": [
            {"id": "r1", "name": "R1", "remote": "x"},
        ]
    }
    path = tmp_path / "repos.yaml"
    path.write_text(yaml.dump(config))
    cfg = load_config(path)
    assert cfg.mode == "deliberate"


# ── TSO save/load roundtrip ──────────────────────────────────────────────────

def test_tso_save_load_roundtrip(tmp_path):
    """TSO should survive a save/load cycle with all fields intact."""
    with mock.patch("switchboard.cli.TSO_DIR", tmp_path):
        tso = load_tso("test-jack-1")
        assert tso["jack_id"] == "test-jack-1"
        assert tso["goal"] is None

        tso["goal"] = "Fix the widget parser"
        tso["assumptions"] = ["input is UTF-8", "max 10MB"]
        tso["hypotheses"] = ["off-by-one in line counter"]
        tso["dead_ends"] = [{"what": "regex approach", "why": "too slow"}]
        tso["next_action"] = {"action": "add unit test", "why": "reproduce first"}

        path = save_tso("test-jack-1", tso)
        assert path.exists()

        loaded = load_tso("test-jack-1")
        assert loaded["goal"] == "Fix the widget parser"
        assert loaded["assumptions"] == ["input is UTF-8", "max 10MB"]
        assert loaded["hypotheses"] == ["off-by-one in line counter"]
        assert loaded["dead_ends"] == [{"what": "regex approach", "why": "too slow"}]
        assert loaded["next_action"]["action"] == "add unit test"
        assert loaded["updated_at"] is not None


# ── Hold listing with mocked bd ──────────────────────────────────────────────

def test_hold_list_with_mocked_bd():
    """sw checkpoint list should parse bd JSON output."""
    mock_jacks = [
        {
            "id": "jack-aaaa",
            "title": "Review API design",
            "issue_type": "checkpoint",
            "status": "open",
            "assignee": "kale",
            "description": "Review the API before proceeding",
            "labels": [],
        },
        {
            "id": "jack-bbbb",
            "title": "Regular jack",
            "issue_type": "jack",
            "status": "open",
            "assignee": "cleo",
            "description": "",
            "labels": [],
        },
    ]

    mock_result = mock.Mock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(mock_jacks)

    with mock.patch("switchboard.checkpoint._run_bd", return_value=mock_result):
        from switchboard.checkpoint import list_open_holds
        holds = list_open_holds()
        assert len(holds) == 1
        assert holds[0].id == "jack-aaaa"
        assert holds[0].title == "Review API design"


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


# ── sw create ────────────────────────────────────────────────────────────────

def test_create_task(tmp_path):
    """sw create should call bd create and log event."""
    log_path = tmp_path / "sessions.jsonl"
    runner = CliRunner()

    mock_result = mock.Mock()
    mock_result.returncode = 0
    mock_result.stdout = "jack-xxxx"
    mock_result.stderr = ""

    with mock.patch("switchboard.cli._run_bd", return_value=mock_result) as mock_bd, \
         mock.patch("switchboard.cli._try_load_config") as mock_cfg:
        cfg = mock.Mock()
        cfg.sessions_log_path = log_path
        mock_cfg.return_value = cfg

        result = runner.invoke(main, ["create", "Test task", "-t", "task", "-p", "1"])
        assert result.exit_code == 0
        assert "Created jack" in result.output

    # Check bd was called with right args
    call_args = mock_bd.call_args[0]
    assert "create" in call_args
    assert "--title=Test task" in call_args
    assert "--type=task" in call_args
    assert "--priority=1" in call_args

    # Check event logged
    events = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["event"] == "jack_create"
    assert events[0]["title"] == "Test task"


def test_create_checkpoint_adds_label(tmp_path):
    """sw create checkpoint should add checkpoint label and set assignee."""
    log_path = tmp_path / "sessions.jsonl"
    runner = CliRunner()

    mock_result = mock.Mock()
    mock_result.returncode = 0
    mock_result.stdout = "jack-yyyy"
    mock_result.stderr = ""

    with mock.patch("switchboard.cli._run_bd", return_value=mock_result) as mock_bd, \
         mock.patch("switchboard.cli._try_load_config") as mock_cfg:
        cfg = mock.Mock()
        cfg.sessions_log_path = log_path
        mock_cfg.return_value = cfg

        result = runner.invoke(main, ["create", "Review design", "-t", "checkpoint", "--requires", "kale"])
        assert result.exit_code == 0

    call_args = mock_bd.call_args[0]
    assert "--type=checkpoint" in call_args
    assert "--assignee=kale" in call_args
    assert "--label" in call_args
    # checkpoint label should be present
    label_idx = [i for i, a in enumerate(call_args) if a == "--label"]
    label_values = [call_args[i + 1] for i in label_idx]
    assert "checkpoint" in label_values


# ── sw show ──────────────────────────────────────────────────────────────────

def test_show_basic(tmp_path):
    """sw show should display formatted jack info."""
    runner = CliRunner()

    mock_jack = {
        "id": "jack-1234",
        "title": "Fix the widget",
        "status": "open",
        "priority": 2,
        "issue_type": "task",
        "assignee": "cleo",
        "created_at": "2026-04-29T10:00:00",
        "labels": ["component-lib"],
        "description": "Widget is broken",
    }
    mock_deps = []

    def mock_bd_json(*args):
        if "show" in args:
            return mock_jack
        if "dep" in args:
            return mock_deps
        return []

    with mock.patch("switchboard.cli._run_bd_json", side_effect=mock_bd_json), \
         mock.patch("switchboard.cli.load_tso", return_value={}):
        result = runner.invoke(main, ["show", "jack-1234"])
        assert result.exit_code == 0
        assert "Fix the widget" in result.output
        assert "cleo" in result.output
        assert "component-lib" in result.output


def test_show_raw():
    """sw show --raw should print raw bd output."""
    runner = CliRunner()

    mock_result = mock.Mock()
    mock_result.returncode = 0
    mock_result.stdout = "raw bd output here"
    mock_result.stderr = ""

    with mock.patch("switchboard.cli._run_bd", return_value=mock_result):
        result = runner.invoke(main, ["show", "jack-1234", "--raw"])
        assert result.exit_code == 0
        assert "raw bd output here" in result.output


# ── sw init ──────────────────────────────────────────────────────────────────

def test_init_creates_repos_yaml(tmp_path, monkeypatch):
    """sw init should create repos.yaml and run bd init --stealth."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    mock_result = mock.Mock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    with mock.patch("switchboard.cli._run_bd", return_value=mock_result) as mock_bd:
        result = runner.invoke(main, ["init"])
        assert result.exit_code == 0
        assert "initialised" in result.output.lower() or "Created repos.yaml" in result.output

    # Check bd init --stealth was called
    call_args = mock_bd.call_args[0]
    assert "init" in call_args
    assert "--stealth" in call_args

    # Check repos.yaml created
    repos_yaml = tmp_path / "repos.yaml"
    assert repos_yaml.exists()
    with open(repos_yaml) as f:
        data = yaml.safe_load(f)
    assert data["mode"] == "deliberate"
    assert data["repos"] == []


def test_init_skips_existing_repos_yaml(tmp_path, monkeypatch):
    """sw init should not overwrite existing repos.yaml."""
    monkeypatch.chdir(tmp_path)
    repos_yaml = tmp_path / "repos.yaml"
    repos_yaml.write_text("mode: prototype\nrepos:\n- id: existing\n")
    runner = CliRunner()

    mock_result = mock.Mock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    with mock.patch("switchboard.cli._run_bd", return_value=mock_result):
        result = runner.invoke(main, ["init"])
        assert result.exit_code == 0
        assert "already exists" in result.output

    # Original content preserved
    with open(repos_yaml) as f:
        data = yaml.safe_load(f)
    assert data["mode"] == "prototype"


# ── sw repo add / list ───────────────────────────────────────────────────────

def test_repo_add(tmp_path, monkeypatch):
    """sw repo add should append repo entry to repos.yaml."""
    monkeypatch.chdir(tmp_path)
    repos_yaml = tmp_path / "repos.yaml"
    repos_yaml.write_text(yaml.dump({"mode": "deliberate", "repos": []}))
    runner = CliRunner()

    result = runner.invoke(main, ["repo", "add", "my-lib", "git@github.com:me/lib.git",
                                  "--name", "My Library", "--version", "1.0"])
    assert result.exit_code == 0
    assert "Added repo" in result.output

    with open(repos_yaml) as f:
        data = yaml.safe_load(f)
    assert len(data["repos"]) == 1
    assert data["repos"][0]["id"] == "my-lib"
    assert data["repos"][0]["remote"] == "git@github.com:me/lib.git"
    assert data["repos"][0]["version"] == "1.0"


def test_repo_add_duplicate(tmp_path, monkeypatch):
    """sw repo add should reject duplicate IDs."""
    monkeypatch.chdir(tmp_path)
    repos_yaml = tmp_path / "repos.yaml"
    repos_yaml.write_text(yaml.dump({"mode": "deliberate", "repos": [{"id": "dupe", "name": "D", "remote": "x"}]}))
    runner = CliRunner()

    result = runner.invoke(main, ["repo", "add", "dupe", "git@x.git"])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_repo_list(tmp_path, monkeypatch):
    """sw repo list should show registered repos."""
    monkeypatch.chdir(tmp_path)
    repos_yaml = tmp_path / "repos.yaml"
    repos_yaml.write_text(yaml.dump({
        "mode": "deliberate",
        "repos": [
            {"id": "alpha", "name": "Alpha", "remote": "git@a.git", "version": "2.0"},
            {"id": "beta", "name": "Beta", "remote": "git@b.git"},
        ],
    }))
    runner = CliRunner()

    result = runner.invoke(main, ["repo", "list"])
    assert result.exit_code == 0
    assert "alpha" in result.output
    assert "beta" in result.output


# ── sw log ───────────────────────────────────────────────────────────────────

def test_log_basic(tmp_path):
    """sw log should show recent jacks and session events."""
    log_path = tmp_path / "sessions.jsonl"
    log_path.write_text(json.dumps({
        "event": "session_start",
        "actor": "kale",
        "timestamp": "2026-04-29T10:00:00",
    }) + "\n")

    runner = CliRunner()

    mock_jacks = [
        {
            "id": "jack-abcd",
            "title": "Fix deploy",
            "status": "open",
            "priority": 1,
            "updated_at": "2026-04-29T11:00:00",
        },
    ]

    def mock_bd_json(*args):
        if "list" in args:
            return mock_jacks
        return []

    with mock.patch("switchboard.cli._run_bd_json", side_effect=mock_bd_json), \
         mock.patch("switchboard.cli._try_load_config") as mock_cfg:
        cfg = mock.Mock()
        cfg.sessions_log_path = log_path
        mock_cfg.return_value = cfg

        result = runner.invoke(main, ["log"])
        assert result.exit_code == 0
        assert "jack-abcd" in result.output
        assert "session_start" in result.output


# ── sw done --decision ────────────────────────────────────────────────────────

def test_done_with_decision_flag():
    """sw done --decision should pass decision value through to index_jack_completion."""
    runner = CliRunner()

    mock_bd_result = mock.Mock()
    mock_bd_result.returncode = 0
    mock_bd_result.stdout = ""
    mock_bd_result.stderr = ""

    mock_client = mock.Mock()
    mock_index = mock.Mock()

    with mock.patch("switchboard.cli._run_bd", return_value=mock_bd_result), \
         mock.patch("switchboard.cli._check_triggers_update"), \
         mock.patch("switchboard.cli._try_load_config", return_value=None), \
         mock.patch("switchboard.qdrant.get_client", return_value=mock_client), \
         mock.patch("switchboard.qdrant.ensure_collection"), \
         mock.patch("switchboard.qdrant.index_jack_completion", mock_index):
        result = runner.invoke(main, [
            "done", "jack-test1",
            "-m", "feat: added widget",
            "-D", "key insight: use recursion not iteration",
        ])
        assert result.exit_code == 0
        assert "closed" in result.output

    # Verify index_jack_completion was called with decision
    mock_index.assert_called_once()
    call_kwargs = mock_index.call_args
    # Could be positional or keyword — check keyword args
    assert call_kwargs[1]["decision"] == "key insight: use recursion not iteration"
    assert call_kwargs[1]["commit_msg"] == "feat: added widget"


# ── sw resume similar jacks ──────────────────────────────────────────────────

def test_resume_shows_similar_jacks(tmp_path):
    """sw resume should show similar past jacks with decision notes."""
    runner = CliRunner()

    mock_similar = [
        {"jack_id": "jack-past1", "title": "Fix parser bug", "decision": "switched to SAX parser"},
        {"jack_id": "jack-past2", "title": "Refactor lexer", "decision": "split into two passes"},
    ]

    mock_jack_info = {
        "id": "jack-resume1",
        "title": "Fix compiler issue",
        "description": "Compiler crashes on nested expressions",
        "status": "in_progress",
    }

    def mock_bd_json(*args):
        if "show" in args:
            return mock_jack_info
        return []

    with mock.patch("switchboard.cli.TSO_DIR", tmp_path), \
         mock.patch("switchboard.cli._run_bd_json", side_effect=mock_bd_json), \
         mock.patch("switchboard.cli._try_load_config", return_value=None), \
         mock.patch("switchboard.qdrant.get_client"), \
         mock.patch("switchboard.qdrant.search_similar_done_jacks", return_value=mock_similar):
        result = runner.invoke(main, ["resume", "jack-resume1"])
        assert result.exit_code == 0
        assert "Similar past jacks:" in result.output
        assert "jack-past1" in result.output
        assert "switched to SAX parser" in result.output
        assert "jack-past2" in result.output
        assert "split into two passes" in result.output
