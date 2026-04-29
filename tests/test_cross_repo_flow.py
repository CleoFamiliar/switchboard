"""Tests for cross-repo triggers_update flow.

Scenario: component-lib jack completes -> triggers_update -> main-app jack
shows update-required state.

All bd CLI calls are mocked — no real beads instance needed.
"""

import json
from unittest import mock

import pytest
from click.testing import CliRunner

from switchboard.cli import main, _check_triggers_update
from switchboard.repos import find_triggers_update_jacks
from switchboard.config import Config, RepoConfig


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_bd_mock(jacks_by_command: dict):
    """Return a side_effect function that routes bd commands to mock responses.

    jacks_by_command maps a command key (tuple of first N args) to the JSON
    response that bd would return.
    """
    def side_effect(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        # cmd is the full argv list: ["bd", "list", "--json", ...]
        # Strip "bd" prefix if present
        parts = [a for a in cmd if isinstance(a, str)]
        if parts and parts[0] == "bd":
            parts = parts[1:]

        result = mock.Mock()
        result.returncode = 0
        result.stderr = ""

        # Match against registered command patterns
        for key, response in jacks_by_command.items():
            key_parts = key if isinstance(key, tuple) else (key,)
            if all(k in parts for k in key_parts):
                result.stdout = json.dumps(response) if response is not None else ""
                return result

        # Default: empty success
        result.stdout = "[]"
        return result

    return side_effect


@pytest.fixture
def two_repo_config(tmp_path):
    """Config with component-lib and main-app repos."""
    return Config(
        repos=[
            RepoConfig(
                id="component-lib",
                name="component-lib",
                remote="git@github.com:example/component-lib.git",
                local_path=str(tmp_path / "component-lib"),
                version="2.0.4",
            ),
            RepoConfig(
                id="main-app",
                name="main-app",
                remote="git@github.com:example/main-app.git",
                local_path=str(tmp_path / "main-app"),
            ),
        ],
        sessions_log_path=tmp_path / "sessions.jsonl",
    )


# ── find_triggers_update_jacks ───────────────────────────────────────────────


class TestFindTriggersUpdateJacks:
    """Test the triggers_update discovery logic in repos.py."""

    def test_finds_jack_with_triggers_update_label(self):
        """A jack labeled triggers_update targeting main-app should be found."""
        mock_jacks = [
            {
                "id": "beads-0001",
                "title": "Update component-lib dep in main-app",
                "status": "open",
                "labels": ["triggers_update", "component-lib", "main-app"],
                "description": "component-lib v2.1 published, main-app needs update",
            },
            {
                "id": "beads-0002",
                "title": "Unrelated jack",
                "status": "open",
                "labels": ["main-app"],
                "description": "Some other work",
            },
        ]
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_jacks)

        with mock.patch("switchboard.repos._run_bd", return_value=mock_result):
            ids = find_triggers_update_jacks(to_repo_id="main-app")
            assert ids == ["beads-0001"]

    def test_finds_jack_with_triggers_update_in_title(self):
        """triggers_update in title should also match."""
        mock_jacks = [
            {
                "id": "beads-0003",
                "title": "triggers_update: component-lib -> main-app",
                "status": "open",
                "labels": [],
                "description": "",
            },
        ]
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_jacks)

        with mock.patch("switchboard.repos._run_bd", return_value=mock_result):
            ids = find_triggers_update_jacks(to_repo_id="main-app")
            assert ids == ["beads-0003"]

    def test_finds_jack_with_triggers_update_in_description(self):
        """triggers_update in description should also match."""
        mock_jacks = [
            {
                "id": "beads-0004",
                "title": "Update main-app deps",
                "status": "open",
                "labels": ["main-app"],
                "description": "This is a triggers_update from component-lib",
            },
        ]
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_jacks)

        with mock.patch("switchboard.repos._run_bd", return_value=mock_result):
            ids = find_triggers_update_jacks(to_repo_id="main-app")
            assert ids == ["beads-0004"]

    def test_no_match_without_triggers_update(self):
        """Jacks without triggers_update should not be returned."""
        mock_jacks = [
            {
                "id": "beads-0005",
                "title": "Regular task in main-app",
                "status": "open",
                "labels": ["main-app"],
                "description": "Nothing special",
            },
        ]
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_jacks)

        with mock.patch("switchboard.repos._run_bd", return_value=mock_result):
            ids = find_triggers_update_jacks(to_repo_id="main-app")
            assert ids == []

    def test_filters_by_target_repo(self):
        """triggers_update jack for a different repo should not match."""
        mock_jacks = [
            {
                "id": "beads-0006",
                "title": "triggers_update: component-lib -> other-app",
                "status": "open",
                "labels": ["triggers_update", "other-app"],
                "description": "",
            },
        ]
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_jacks)

        with mock.patch("switchboard.repos._run_bd", return_value=mock_result):
            ids = find_triggers_update_jacks(to_repo_id="main-app")
            assert ids == []

    def test_returns_all_when_no_target_filter(self):
        """Without to_repo_id filter, return all triggers_update jacks."""
        mock_jacks = [
            {
                "id": "beads-0007",
                "title": "triggers_update: lib -> app-a",
                "status": "open",
                "labels": ["triggers_update"],
                "description": "",
            },
            {
                "id": "beads-0008",
                "title": "triggers_update: lib -> app-b",
                "status": "open",
                "labels": ["triggers_update"],
                "description": "",
            },
        ]
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_jacks)

        with mock.patch("switchboard.repos._run_bd", return_value=mock_result):
            ids = find_triggers_update_jacks(to_repo_id=None)
            assert ids == ["beads-0007", "beads-0008"]

    def test_handles_bd_failure(self):
        """Gracefully returns empty list when bd fails."""
        mock_result = mock.Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with mock.patch("switchboard.repos._run_bd", return_value=mock_result):
            ids = find_triggers_update_jacks(to_repo_id="main-app")
            assert ids == []

    def test_filters_by_source_repo(self):
        """from_repo_id should be passed as --label to bd."""
        mock_jacks = [
            {
                "id": "beads-0009",
                "title": "triggers_update to main-app",
                "status": "open",
                "labels": ["triggers_update", "component-lib", "main-app"],
                "description": "",
            },
        ]
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_jacks)

        with mock.patch("switchboard.repos._run_bd", return_value=mock_result) as mock_bd:
            ids = find_triggers_update_jacks(
                from_repo_id="component-lib", to_repo_id="main-app"
            )
            assert ids == ["beads-0009"]
            # Verify that --label component-lib was passed to bd
            call_args = mock_bd.call_args[0]
            assert "--label" in call_args
            assert "component-lib" in call_args


# ── sw done triggers_update notification ─────────────────────────────────────


class TestDoneTriggersUpdate:
    """Test that `sw done` checks and reports triggers_update relations."""

    def test_done_reports_triggers_update(self, two_repo_config):
        """Completing a triggers_update jack should print notification."""
        # The jack being closed is a triggers_update jack
        show_response = {
            "id": "beads-t001",
            "title": "triggers_update: component-lib -> main-app",
            "status": "open",
            "labels": ["triggers_update", "component-lib", "main-app"],
            "description": "component-lib v2.1 requires main-app update",
        }

        def bd_side_effect(*args, **kwargs):
            result = mock.Mock()
            result.returncode = 0
            result.stderr = ""
            cmd = list(args)
            if cmd[0] == "bd":
                cmd = cmd[1:]
            if "close" in cmd:
                result.stdout = f"Closed beads-t001"
                return result
            if "show" in cmd:
                result.stdout = json.dumps(show_response)
                return result
            result.stdout = "[]"
            return result

        runner = CliRunner()
        with mock.patch("switchboard.cli._run_bd", side_effect=bd_side_effect), \
             mock.patch("switchboard.cli._run_bd_json", return_value=show_response), \
             mock.patch("switchboard.cli._try_load_config", return_value=two_repo_config):
            result = runner.invoke(main, ["done", "beads-t001"])
            assert result.exit_code == 0
            assert "beads-t001 closed" in result.output
            assert "triggers_update" in result.output
            assert "main-app" in result.output

    def test_done_no_notification_for_regular_jack(self, two_repo_config):
        """A regular jack (no triggers_update) should not show notification."""
        show_response = {
            "id": "beads-r001",
            "title": "Fix button styling",
            "status": "open",
            "labels": ["component-lib"],
            "description": "Button colors are wrong",
        }

        def bd_side_effect(*args, **kwargs):
            result = mock.Mock()
            result.returncode = 0
            result.stderr = ""
            cmd = list(args)
            if cmd[0] == "bd":
                cmd = cmd[1:]
            if "close" in cmd:
                result.stdout = "Closed beads-r001"
                return result
            result.stdout = "[]"
            return result

        runner = CliRunner()
        with mock.patch("switchboard.cli._run_bd", side_effect=bd_side_effect), \
             mock.patch("switchboard.cli._run_bd_json", return_value=show_response), \
             mock.patch("switchboard.cli._try_load_config", return_value=two_repo_config):
            result = runner.invoke(main, ["done", "beads-r001"])
            assert result.exit_code == 0
            assert "triggers_update" not in result.output


# ── Full cross-repo flow ─────────────────────────────────────────────────────


class TestCrossRepoFlow:
    """End-to-end cross-repo triggers_update flow with mocked bd.

    Scenario:
    1. component-lib has a jack for "publish v2.1"
    2. main-app has a jack "update component-lib dep" with triggers_update
       label, depending on the component-lib jack
    3. component-lib jack is completed via sw done
    4. find_triggers_update_jacks shows main-app has a pending update
    5. sw done on the triggers_update jack reports the downstream notification
    """

    def test_full_cross_repo_triggers_update_flow(self, two_repo_config):
        """Component-lib completion triggers update notification for main-app."""
        # -- Step 1: component-lib jack exists
        comp_jack = {
            "id": "beads-comp-001",
            "title": "Publish component-lib v2.1",
            "status": "open",
            "labels": ["component-lib"],
            "description": "Release new version of shared components",
            "issue_type": "feature",
            "priority": 1,
        }

        # -- Step 2: main-app triggers_update jack exists, blocked
        app_jack = {
            "id": "beads-app-001",
            "title": "Update component-lib dep in main-app",
            "status": "open",
            "labels": ["triggers_update", "main-app", "component-lib"],
            "description": "triggers_update: when component-lib v2.1 is published, update main-app",
            "issue_type": "task",
            "priority": 2,
        }

        all_jacks = [comp_jack, app_jack]

        # -- Step 3: Before closing comp jack, find_triggers_update_jacks should
        # find app_jack as a pending update for main-app
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(all_jacks)

        with mock.patch("switchboard.repos._run_bd", return_value=mock_result):
            pending = find_triggers_update_jacks(to_repo_id="main-app")
            assert "beads-app-001" in pending
            assert "beads-comp-001" not in pending  # comp jack is not a trigger

        # -- Step 4: Close the component-lib jack via sw done
        def bd_side_effect(*args, **kwargs):
            result = mock.Mock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = "Closed beads-comp-001"
            return result

        runner = CliRunner()
        with mock.patch("switchboard.cli._run_bd", side_effect=bd_side_effect), \
             mock.patch("switchboard.cli._run_bd_json", return_value=comp_jack), \
             mock.patch("switchboard.cli._try_load_config", return_value=two_repo_config):
            result = runner.invoke(main, [
                "done", "beads-comp-001",
                "-m", "feat: publish component-lib v2.1",
                "-d", "New Button and Modal components",
            ])
            assert result.exit_code == 0
            assert "beads-comp-001 closed" in result.output
            # Regular jack — no triggers_update notification
            assert "triggers_update" not in result.output

        # -- Step 5: Now close the triggers_update jack via sw done
        def bd_side_effect_trigger(*args, **kwargs):
            result = mock.Mock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = "Closed beads-app-001"
            return result

        with mock.patch("switchboard.cli._run_bd", side_effect=bd_side_effect_trigger), \
             mock.patch("switchboard.cli._run_bd_json", return_value=app_jack), \
             mock.patch("switchboard.cli._try_load_config", return_value=two_repo_config):
            result = runner.invoke(main, [
                "done", "beads-app-001",
                "-m", "chore: update component-lib to v2.1",
                "-d", "Bumped component-lib dep",
            ])
            assert result.exit_code == 0
            assert "beads-app-001 closed" in result.output
            # This IS a triggers_update jack — should show notification
            assert "triggers_update" in result.output
            assert "main-app" in result.output

    def test_triggers_update_from_specific_source_repo(self, two_repo_config):
        """find_triggers_update_jacks with from_repo_id filters by source."""
        trigger_jack = {
            "id": "beads-tu-001",
            "title": "triggers_update: component-lib -> main-app",
            "status": "open",
            "labels": ["triggers_update", "component-lib", "main-app"],
            "description": "",
        }
        unrelated_jack = {
            "id": "beads-tu-002",
            "title": "triggers_update: other-lib -> main-app",
            "status": "open",
            "labels": ["triggers_update", "other-lib", "main-app"],
            "description": "",
        }

        # When filtering by from_repo_id=component-lib, bd gets --label component-lib
        # so only trigger_jack should be in the bd response
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps([trigger_jack])

        with mock.patch("switchboard.repos._run_bd", return_value=mock_result):
            ids = find_triggers_update_jacks(
                from_repo_id="component-lib", to_repo_id="main-app"
            )
            assert ids == ["beads-tu-001"]

    def test_pending_updates_empty_after_all_triggers_closed(self):
        """After all triggers_update jacks are closed, none should be pending."""
        # bd returns no open jacks
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps([])

        with mock.patch("switchboard.repos._run_bd", return_value=mock_result):
            ids = find_triggers_update_jacks(to_repo_id="main-app")
            assert ids == []
