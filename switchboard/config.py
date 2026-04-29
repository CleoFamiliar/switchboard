"""
config.py — Configuration loading and validation for Switchboard.

Loads `repos.yaml` (or a custom path) from the workspace root and exposes
a typed Config object used throughout the CLI.

Responsibilities:
- Find and load repos.yaml (walk up from cwd, then fallback to ~)
- Parse repo entries, hold defaults, qdrant settings, session log path
- Parse workspace mode (prototype | deliberate)
- Validate required fields (repo IDs must be unique, paths must be strings)
- Expand ~ in local_path values
- Provide a `get_repo(id)` helper for lookups

Config is loaded once per CLI invocation and passed via Click context.

Modes:
  deliberate — all holds require explicit operator ack (safe default)
  prototype  — holds with requires:any auto-ack; only requires:kale still blocks
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
class HoldDefaults:
    requires: str = "kale"
    publish_requires: str = "kale"
    deploy_requires: str = "kale"
    test_requires: str = "any"


# Legacy alias
CheckpointDefaults = HoldDefaults


@dataclass
class QdrantConfig:
    host: str = "localhost"
    port: int = 6333
    collection: str = "switchyard"


@dataclass
class Config:
    repos: list[RepoConfig] = field(default_factory=list)
    checkpoint_defaults: HoldDefaults = field(default_factory=HoldDefaults)
    qdrant: QdrantConfig = field(default_factory=QdrantConfig)
    sessions_log_path: Path = Path("~/.openclaw/workspace/orchestration/sessions.jsonl")
    mode: str = "deliberate"  # "deliberate" | "prototype"

    def get_repo(self, repo_id: str) -> Optional[RepoConfig]:
        """Return repo config by ID, or None if not found."""
        return next((r for r in self.repos if r.id == repo_id), None)


def _find_repos_yaml(start: Path) -> Optional[Path]:
    """Walk up from start dir looking for repos.yaml."""
    current = start.resolve()
    while True:
        candidate = current / "repos.yaml"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def load_config(path: Optional[Path] = None) -> Config:
    """Load and parse repos.yaml.

    Searches for repos.yaml starting from cwd, walking up to filesystem root.
    Raises FileNotFoundError if no config file is found.
    """
    if path is None:
        path = _find_repos_yaml(Path.cwd())
    if path is None or not path.exists():
        raise FileNotFoundError(
            "repos.yaml not found. Searched from cwd upward. "
            "Create one or pass --config explicitly."
        )

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    repos = []
    for r in raw.get("repos", []):
        lp = r.get("local_path")
        if lp:
            lp = str(Path(lp).expanduser())
        repos.append(RepoConfig(
            id=r["id"],
            name=r.get("name", r["id"]),
            remote=r.get("remote", ""),
            local_path=lp,
            version=r.get("version"),
        ))

    # Validate unique IDs
    ids = [r.id for r in repos]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate repo IDs in {path}: {ids}")

    cp_raw = raw.get("checkpoint_defaults", {})
    cp = HoldDefaults(
        requires=cp_raw.get("requires", "kale"),
        publish_requires=cp_raw.get("publish_requires", "kale"),
        deploy_requires=cp_raw.get("deploy_requires", "kale"),
        test_requires=cp_raw.get("test_requires", "any"),
    )

    q_raw = raw.get("qdrant", {})
    qdrant = QdrantConfig(
        host=q_raw.get("host", "localhost"),
        port=q_raw.get("port", 6333),
        collection=q_raw.get("collection", "switchyard"),
    )

    sessions_raw = raw.get("sessions", {})
    log_path = Path(sessions_raw.get(
        "log_path",
        "~/.openclaw/workspace/orchestration/sessions.jsonl",
    )).expanduser()

    workspace_mode = raw.get("mode", "deliberate")
    if workspace_mode not in ("deliberate", "prototype"):
        workspace_mode = "deliberate"

    return Config(
        repos=repos,
        checkpoint_defaults=cp,
        qdrant=qdrant,
        sessions_log_path=log_path,
        mode=workspace_mode,
    )
