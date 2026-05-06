"""Base skill abstraction for event-driven cross-repo actions."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class Confidence(Enum):
    HIGH = "high"      # rule matched — act automatically
    MEDIUM = "medium"  # partial match — act but notify
    LOW = "low"        # LLM inferred — notify, require confirmation


@dataclass
class SkillResult:
    skill: str
    confidence: Confidence
    action: str          # human-readable description of what was done/proposed
    jack_ids: list[str] = field(default_factory=list)
    auto_applied: bool = False  # True if already acted, False if needs confirmation


class BaseSkill(ABC):
    name: str
    description: str

    @abstractmethod
    def should_run(self, event: dict) -> bool:
        """Return True if this skill is relevant to the event."""

    @abstractmethod
    def run(self, event: dict, config) -> list[SkillResult]:
        """Execute skill logic. Return list of results (may be empty)."""
