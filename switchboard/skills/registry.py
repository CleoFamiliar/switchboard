"""Skill registry — central dispatcher for event-driven skills."""

from .base import BaseSkill, SkillResult


class SkillRegistry:
    """Holds registered skills and dispatches events to them."""

    def __init__(self) -> None:
        self._skills: list[BaseSkill] = []

    def register(self, skill: BaseSkill) -> None:
        self._skills.append(skill)

    @property
    def skills(self) -> list[BaseSkill]:
        return list(self._skills)

    def run_all(self, event: dict, config) -> list[SkillResult]:
        """Run all skills that match the event, collecting results."""
        results: list[SkillResult] = []
        for skill in self._skills:
            if skill.should_run(event):
                results.extend(skill.run(event, config))
        return results
