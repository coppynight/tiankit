"""
Watchdog 模块：Evidence 校验与裁决建议
"""
import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class WatchdogConfig:
    """Watchdog 配置"""
    project_root: Path
    deny_commands: List[str] = None
    deny_paths: List[str] = None
    required_evidence_fields: List[str] = None


@dataclass
class EvidenceInfo:
    """证据信息"""
    evidence_path: str
    patch_path: str
    evidence_digest: str
    patch_digest: str
    path_safety: Dict[str, Any]


@dataclass
class VerdictResult:
    """裁决结果"""
    verdict: str  # PASS / WARN / BLOCK
    reasons: List[str]
    suggested_actions: List[str]
    details: Dict[str, Any]


class Watchdog:
    """合规审计官：检查 Evidence 并输出裁决"""
    
    def __init__(self, config: WatchdogConfig):
        self.config = config
        self.deny_commands = config.deny_commands or []
        self.deny_paths = config.deny_paths or []
        self.required_fields = config.required_evidence_fields or [
            "evidencePath", "patchPath", "evidenceDigest", "patchDigest", "pathSafety"
        ]
    
    def _sha256_file(self, path: Path) -> str:
        """计算文件 SHA256"""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return f"sha256:{h.hexdigest()}"
    
    def _verify_digest(self, path: Path, expected_digest: str) -> Tuple[bool, str]:
        """验证文件 digest"""
        if not path.exists():
            return False, "file_not_found"
        actual = self._sha256_file(path)
        if actual != expected_digest:
            return False, "digest_mismatch"
        return True, "ok"
    
    def _check_path_safety(self, path_safety: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """检查路径安全性"""
        issues = []
        
        pwd = path_safety.get("pwd", "")
        repo_root = path_safety.get("repoRoot", "")
        changed_files = path_safety.get("changedFiles", [])
        
        # 检查是否在 repoRoot 下
        try:
            real_pwd = Path(pwd).resolve()
            real_repo_root = Path(repo_root).resolve()
            if not str(real_pwd).startswith(str(real_repo_root)):
                issues.append(f"pwd outside repo: {pwd}")
        except Exception as e:
            issues.append(f"pwd resolve error: {e}")
        
        # 检查 changedFiles 是否在 repoRoot 内
        for f in changed_files:
            try:
                # 如果是相对路径，尝试与 repoRoot 组合
                if Path(f).is_absolute():
                    full_path = Path(f).resolve()
                else:
                    full_path = Path(repo_root, f).resolve()
                if not str(full_path).startswith(str(Path(repo_root).resolve())):
                    issues.append(f"changed file outside repo: {f}")
            except Exception:
                issues.append(f"changed file resolve error: {f}")
        
        return len(issues) == 0, issues
    
    def _check_deny_commands(self, commands: List[Dict[str, str]]) -> Tuple[bool, List[str]]:
        """检查是否包含禁止命令"""
        issues = []
        
        for cmd in commands:
            cmd_str = cmd.get("cmd", "")
            # 检查 deny_commands
            for deny in self.deny_commands:
                if cmd_str.startswith(deny):
                    issues.append(f"deny command: {cmd_str}")
        
        return len(issues) == 0, issues
    
    def verify_evidence(self, evidence: EvidenceInfo, project_dir: Path) -> VerdictResult:
        """
        验证证据并输出裁决
        
        检查项：
        1. Evidence digest 校验
        2. Patch digest 校验
        3. Path safety 检查
        4. Required fields 检查
        """
        reasons = []
        suggested_actions = []
        details = {}
        
        # 1. 检查 required fields（使用 dataclass 的 __dataclass_fields__）
        missing_fields = []
        from dataclasses import fields
        for field in fields(evidence):
            if field.name in self.required_fields:
                value = getattr(evidence, field.name)
                if value is None or value == "" or (field.name == "path_safety" and not value):
                    missing_fields.append(field.name)
        
        if missing_fields:
            reasons.append(f"missing required fields: {missing_fields}")
            suggested_actions.append("submit_complete_evidence")
            details["missing_fields"] = missing_fields
        
        # 2. 验证 evidence digest
        evidence_path = project_dir / evidence.evidence_path
        ok, error = self._verify_digest(evidence_path, evidence.evidence_digest)
        if not ok:
            reasons.append(f"evidence digest error: {error}")
            suggested_actions.append("resubmit_evidence")
            details["evidence_digest_error"] = error
        
        # 3. 验证 patch digest
        if evidence.patch_path:
            patch_path = project_dir / evidence.patch_path
            ok, error = self._verify_digest(patch_path, evidence.patch_digest)
            if not ok:
                reasons.append(f"patch digest error: {error}")
                suggested_actions.append("resubmit_evidence")
                details["patch_digest_error"] = error
        
        # 4. 检查 path safety
        if evidence.path_safety:
            ok, issues = self._check_path_safety(evidence.path_safety)
            if not ok:
                for issue in issues:
                    reasons.append(f"path_safety violation: {issue}")
                suggested_actions.append("check_workspace")
                details["path_safety_issues"] = issues
        
        # 5. 生成裁决
        if len(reasons) == 0:
            verdict = "PASS"
            details["check_passed"] = True
        elif any("deny command" in r for r in reasons):
            verdict = "BLOCK"
            suggested_actions.append("halt_project")
        elif any("outside repo" in r for r in reasons):
            verdict = "BLOCK"
            suggested_actions.append("halt_project")
        elif any("digest" in r for r in reasons):
            verdict = "WARN"
            suggested_actions.append("resubmit_evidence")
        else:
            verdict = "WARN"
            suggested_actions.append("investigate")
        
        return VerdictResult(
            verdict=verdict,
            reasons=reasons,
            suggested_actions=suggested_actions,
            details=details
        )
    
    def evaluate(self, task_spec: Dict[str, Any], evidence: Dict[str, Any], project_dir: Path) -> Dict[str, Any]:
        """
        评估任务证据并返回裁决

        Args:
            task_spec: 任务规格
            evidence: 证据字典
            project_dir: 项目目录

        Returns:
            Verdict 事件 payload
        """
        # 构建 EvidenceInfo
        evidence_info = EvidenceInfo(
            evidence_path=evidence.get("evidencePath", ""),
            patch_path=evidence.get("patchPath", ""),
            evidence_digest=evidence.get("evidenceDigest", ""),
            patch_digest=evidence.get("patchDigest", ""),
            path_safety=evidence.get("pathSafety", {}),
        )
        
        # 执行校验
        result = self.verify_evidence(evidence_info, project_dir)
        
        # 返回裁决 payload
        return {
            "verdict": result.verdict,
            "reasons": result.reasons,
            "suggestedActions": result.suggested_actions,
            "details": result.details,
            "checkedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
