"""
config.py — Configuration loading and validation for Switchyard.

Loads `repos.yaml` (or a custom path) from the workspace root and exposes
a typed Config object used throughout the CLI.

Responsibilities:
- Find and load repos.yaml (walk up from cwd, then fallback to ~)
- Parse repo entries, checkpoint defaults, qdrant settings, session log path
- Validate required fields (repo IDs must be unique, paths must be strings)
- Expand ~ in local_path values
- Provide a `get_repo(id)` helper for lookups

Config is loaded once per CLI invocation and passed via Click context.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import yaml


@dataclass
class RepoConfig:
    id: str
    name: str
    remote: str
    local_path: Optional[str] = None
    version: Optional[str] = None


@dataclass
class CheckpointDefaults:
    requires: str = "kale"
    publish_requires: str = "kale"
    deploy_requires: str = "kale"
    test_requires: str = "any"


@dataclass
class QdrantConfig:
    host: str = "localhost"
    port: int = 6333
    collection: str = "switchyard"


@dataclass
class Config:
    repos: list[RepoConfig] = field(default_factory=list)
    checkpoint_defaults: CheckpointDefaults = field(default_factory=CheckpointDefaults)
    qdrant: QdrantConfig = field(default_factory=QdrantConfig)
    sessions_log_path: Path = Path("~/.openclaw/workspace/orchestration/sessions.jsonl")

    def get_repo(self, repo_id: str) -> Optional[RepoConfig]:
        """Return repo config by ID, or None if not found."""
        return next((r for r in self.repos if r.id == repo_id), None)


def load_config(path: Optional[Path] = None) -> Config:
    """Load and parse repos.yaml.

    Searches for repos.yaml starting from cwd, walking up to home dir.
    Raises FileNotFoundError if no config file is found.
    """
    raise NotImplementedError("load_config: not yet implemented")
