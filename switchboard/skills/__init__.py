"""Skills — pluggable event-driven actions for cross-repo orchestration."""

from .base import BaseSkill, Confidence, SkillResult
from .registry import SkillRegistry

__all__ = ["BaseSkill", "Confidence", "SkillResult", "SkillRegistry"]
