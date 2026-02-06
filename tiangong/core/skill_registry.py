from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class EvidenceContract:
    requiresPatch: bool = True
    requiresCommands: bool = True
    requiresValidationScript: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskPolicy:
    tier: str = "safe"  # safe | networked | privileged
    allowedOps: List[str] = field(default_factory=list)
    denyPaths: List[str] = field(default_factory=list)
    allowNetwork: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillSpec:
    skillName: str
    supportedKinds: List[str]
    invocationHints: Optional[str] = None
    inputSchema: Optional[Dict[str, Any]] = None
    evidenceContract: Optional[EvidenceContract] = None
    riskPolicy: Optional[RiskPolicy] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.evidenceContract:
            data["evidenceContract"] = asdict(self.evidenceContract)
        if self.riskPolicy:
            data["riskPolicy"] = asdict(self.riskPolicy)
        return data

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "SkillSpec":
        evidence = raw.get("evidenceContract")
        risk = raw.get("riskPolicy")
        return SkillSpec(
            skillName=raw.get("skillName", ""),
            supportedKinds=list(raw.get("supportedKinds") or []),
            invocationHints=raw.get("invocationHints"),
            inputSchema=raw.get("inputSchema"),
            evidenceContract=EvidenceContract(**evidence) if isinstance(evidence, dict) else None,
            riskPolicy=RiskPolicy(**risk) if isinstance(risk, dict) else None,
        )


@dataclass
class SkillRegistry:
    skills: Dict[str, SkillSpec] = field(default_factory=dict)

    @staticmethod
    def load(path: Path) -> "SkillRegistry":
        if not path.exists():
            return SkillRegistry()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return SkillRegistry()
        skills = {}
        for item in payload.get("skills", []) or []:
            spec = SkillSpec.from_dict(item)
            if spec.skillName:
                skills[spec.skillName] = spec
        return SkillRegistry(skills=skills)

    def to_dict(self) -> Dict[str, Any]:
        return {"skills": [spec.to_dict() for spec in self.skills.values()]}

    def by_kind(self, kind: Optional[str]) -> List[SkillSpec]:
        if not kind:
            return []
        out = []
        for spec in self.skills.values():
            if kind in (spec.supportedKinds or []):
                out.append(spec)
        return out

    def get(self, name: str) -> Optional[SkillSpec]:
        return self.skills.get(name)
