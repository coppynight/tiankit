from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .skill_registry import SkillRegistry


@dataclass
class SkillSuggestion:
    taskId: str
    kind: Optional[str]
    candidates: List[str]
    preferred: Optional[str]
    remembered: Optional[str]
    suggestedByPM: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SkillRouter:
    def __init__(self, registry: SkillRegistry, skill_memory: Optional[Dict[str, str]] = None):
        self.registry = registry
        self.skill_memory = skill_memory or {}

    def suggest(self, task_spec: Dict[str, Any]) -> SkillSuggestion:
        task_id = task_spec.get("taskId", "")
        kind = task_spec.get("kind")
        suggested_pm = list(task_spec.get("suggestedSkills") or [])

        candidates: List[str] = []
        for name in suggested_pm:
            if name not in candidates:
                candidates.append(name)

        for spec in self.registry.by_kind(kind):
            if spec.skillName not in candidates:
                candidates.append(spec.skillName)

        remembered = self.skill_memory.get(kind) if kind else None
        preferred = task_spec.get("preferredSkill") or remembered
        if not preferred and candidates:
            preferred = candidates[0]

        return SkillSuggestion(
            taskId=task_id,
            kind=kind,
            candidates=candidates,
            preferred=preferred,
            remembered=remembered,
            suggestedByPM=suggested_pm,
        )

    def build_prompt(self, project: str, suggestion: SkillSuggestion) -> str:
        if suggestion.remembered:
            return (
                f"检测到你上次在 {suggestion.kind} 任务使用的是 {suggestion.remembered}。"
                f"\n是否继续使用？确认命令：天工 {project} 选择skill {suggestion.taskId} {suggestion.remembered}"
            )
        if suggestion.preferred:
            return (
                f"建议使用 {suggestion.preferred}。"
                f"\n确认命令：天工 {project} 选择skill {suggestion.taskId} {suggestion.preferred}"
            )
        return f"请为任务 {suggestion.taskId} 选择 skill。"

    @staticmethod
    def update_skill_memory(team_json: Path, kind: str, skill: str) -> None:
        if not team_json.exists():
            return
        data = json.loads(team_json.read_text(encoding="utf-8"))
        defaults = data.setdefault("defaults", {})
        memory = defaults.setdefault("skillMemory", {})
        memory[kind] = skill
        team_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
