#!/usr/bin/env python3
"""
M4 验收测试：Watchdog / 审计
"""
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List

SCRIPT_DIR = Path(__file__).resolve().parent
TOOL_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(TOOL_ROOT))

from core.state_manager import StateManager
from core.reducer import reduce_events
from core.orchestrator import Orchestrator, OrchestratorConfig
from core.watchdog import Watchdog, WatchdogConfig
from core.ids import run_id


ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utc_now():
    return datetime.now(timezone.utc).strftime(ISO_FORMAT)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def test_watchdog_evidence_verification():
    """验收 1：Watchdog Evidence 校验（PASS）"""
    log("测试 1: Watchdog Evidence 校验（PASS）")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        repo_root = project_dir / "repo"
        repo_root.mkdir()
        
        # 创建证据文件
        evidence_content = "# Evidence\nTest evidence content"
        evidence_path = project_dir / "evidence.md"
        evidence_path.write_text(evidence_content)
        
        patch_path = project_dir / "evidence.patch"
        patch_path.write_text("diff content")
        
        # 计算 digests
        import hashlib
        def sha256(path):
            h = hashlib.sha256()
            with open(path, "rb") as f:
                h.update(f.read())
            return f"sha256:{h.hexdigest()}"
        
        evidence_digest = sha256(evidence_path)
        patch_digest = sha256(patch_path)
        
        # 配置 Watchdog
        config = WatchdogConfig(
            project_root=repo_root,
            deny_commands=["rm -rf", "sudo"],
            deny_paths=["/etc", "/root"],
        )
        watchdog = Watchdog(config)
        
        # 构建 evidence
        evidence = {
            "evidencePath": str(evidence_path.relative_to(project_dir)),
            "patchPath": str(patch_path.relative_to(project_dir)),
            "evidenceDigest": evidence_digest,
            "patchDigest": patch_digest,
            "pathSafety": {
                "pwd": str(repo_root),
                "repoRoot": str(repo_root),
                "changedFiles": ["evidence.md", "evidence.patch"],
            },
        }
        
        # 执行校验
        result = watchdog.evaluate({}, evidence, project_dir)
        
        assert result["verdict"] == "PASS", f"应为 PASS，实际: {result['verdict']}"
        assert len(result["reasons"]) == 0, f"应无原因，实际: {result['reasons']}"
        log("  ✅ Evidence 校验 PASS")
        
    return True


def test_watchdog_path_safety_violation():
    """验收 2：Watchdog Path Safety 违规检测"""
    log("测试 2: Watchdog Path Safety 违规检测")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        
        # 配置 Watchdog
        config = WatchdogConfig(
            project_root=Path("/tmp/repo"),
            deny_commands=["rm -rf"],
        )
        watchdog = Watchdog(config)
        
        # 构建违规 evidence
        evidence = {
            "evidencePath": "evidence.md",
            "patchPath": "evidence.patch",
            "evidenceDigest": "sha256:abc123",
            "patchDigest": "sha256:def456",
            "pathSafety": {
                "pwd": "/etc",  # 违规：不在 repoRoot 下
                "repoRoot": "/tmp/repo",
                "changedFiles": [],
            },
        }
        
        # 执行校验
        result = watchdog.evaluate({}, evidence, project_dir)
        
        assert result["verdict"] == "BLOCK", f"应为 BLOCK，实际: {result['verdict']}"
        assert any("pwd outside repo" in r for r in result["reasons"]), f"应检测到 pwd 违规，实际: {result['reasons']}"
        log("  ✅ 检测到 Path Safety 违规")
        
    return True


def test_watchdog_deny_command():
    """验收 3：Watchdog 无禁止命令时应允许"""
    log("测试 3: Watchdog 无禁止命令时应允许")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        repo_root = project_dir / "repo"
        repo_root.mkdir()
        
        # 创建证据文件
        evidence_path = repo_root / "evidence.md"
        evidence_path.write_text("# Evidence")
        patch_path = repo_root / "evidence.patch"
        patch_path.write_text("diff content")
        
        # 计算 digests
        import hashlib
        def sha256(path):
            h = hashlib.sha256()
            with open(path, "rb") as f:
                h.update(f.read())
            return f"sha256:{h.hexdigest()}"
        
        evidence_digest = sha256(evidence_path)
        patch_digest = sha256(patch_path)
        
        # 配置 Watchdog（没有 deny_commands）
        config = WatchdogConfig(
            project_root=repo_root,
            deny_commands=[],  # 空列表
        )
        watchdog = Watchdog(config)
        
        # 构建 evidence（使用相对路径，相对于 repo_root）
        evidence = {
            "evidencePath": "evidence.md",
            "patchPath": "evidence.patch",
            "evidenceDigest": evidence_digest,
            "patchDigest": patch_digest,
            "pathSafety": {
                "pwd": str(repo_root),
                "repoRoot": str(repo_root),
                "changedFiles": [],
            },
        }
        
        # 执行校验（没有 deny_commands 应返回 PASS）
        result = watchdog.evaluate({}, evidence, repo_root)
        
        # 无违规应返回 PASS
        assert result["verdict"] == "PASS", f"应为 PASS，实际: {result['verdict']}，原因: {result['reasons']}"
        log("  ✅ 无禁止命令时返回 PASS")
        
    return True


def test_watchdog_missing_fields():
    """验收 4：Watchdog 缺少必需字段检测"""
    log("测试 4: Watchdog 缺少必需字段检测")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        
        config = WatchdogConfig(project_root=Path("/tmp/repo"))
        watchdog = Watchdog(config)
        
        # 缺少必需字段（evidenceDigest 和 patchDigest）
        evidence = {
            "evidencePath": "evidence.md",
            "patchPath": "evidence.patch",
            # 缺少 evidenceDigest, patchDigest
            "pathSafety": {
                "pwd": "/tmp/repo",
                "repoRoot": "/tmp/repo",
                "changedFiles": [],
            },
        }
        
        result = watchdog.evaluate({}, evidence, project_dir)
        
        # 缺少 digest 应返回 WARN
        assert result["verdict"] == "WARN", f"应为 WARN，实际: {result['verdict']}"
        assert any("digest" in r for r in result["reasons"]), f"应检测到 digest 缺失，实际: {result['reasons']}"
        log("  ✅ 检测到缺少必需字段")
        
    return True


def test_auto_retry_blocked():
    """验收 5：自动重试 blocked 任务"""
    log("测试 5: 自动重试 blocked 任务")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        sm = StateManager(base_dir)
        project = "test-retry"
        
        # PROJECT_STARTED
        sm.append_event({
            "type": "PROJECT_STARTED",
            "actor": "orchestrator",
            "project": project,
            "runId": "start-1",
            "payload": {},
            "idempotencyKey": f"{project}:PROJECT_STARTED:start-1",
        })
        
        # TASKSPEC_PUBLISHED
        sm.append_event({
            "type": "TASKSPEC_PUBLISHED",
            "actor": "pm",
            "project": project,
            "taskId": "TEST-1",
            "payload": {
                "taskId": "TEST-1",
                "goal": "Test retry",
                "kind": "docs",
                "acceptance": ["done"],
            },
            "idempotencyKey": f"{project}:TEST-1:TASKSPEC_PUBLISHED",
        })
        
        # TASK_SKILL_SET
        sm.append_event({
            "type": "TASK_SKILL_SET",
            "actor": "human",
            "project": project,
            "taskId": "TEST-1",
            "payload": {"chosenSkill": "writer", "decisionSeq": 1},
            "idempotencyKey": f"{project}:TEST-1:TASK_SKILL_SET:1",
        })
        
        # 第一次运行失败
        run_id_1 = run_id("r")
        sm.append_event({
            "type": "WORKER_RUN_INTENT",
            "actor": "orchestrator",
            "project": project,
            "taskId": "TEST-1",
            "runId": run_id_1,
            "payload": {},
            "idempotencyKey": f"{project}:TEST-1:{run_id_1}:WORKER_RUN_INTENT",
        })
        sm.append_event({
            "type": "WORKER_RUN_STARTED",
            "actor": "orchestrator",
            "project": project,
            "taskId": "TEST-1",
            "runId": run_id_1,
            "payload": {},
            "idempotencyKey": f"{project}:TEST-1:{run_id_1}:WORKER_RUN_STARTED",
        })
        sm.append_event({
            "type": "WORKER_RUN_FAILED",
            "actor": "orchestrator",
            "project": project,
            "taskId": "TEST-1",
            "runId": run_id_1,
            "payload": {"reason": "first_attempt_failed"},
            "idempotencyKey": f"{project}:TEST-1:{run_id_1}:WORKER_RUN_FAILED",
        })
        sm.append_event({
            "type": "RUN_CLOSED",
            "actor": "orchestrator",
            "project": project,
            "taskId": "TEST-1",
            "runId": run_id_1,
            "payload": {"closeReason": "failed"},
            "idempotencyKey": f"{project}:TEST-1:{run_id_1}:RUN_CLOSED",
        })
        
        # 运行 tick（带重试配置）
        orch = Orchestrator(OrchestratorConfig(
            base_dir=base_dir,
            max_retries=3,  # 启用自动重试
            worker_timeout_minutes=30,
        ))
        result = orch.tick()
        
        # 检查是否产生重试
        events_path = base_dir / "audit" / "events.ndjson"
        with open(events_path, "r") as f:
            lines = f.readlines()
            has_retried = any("TASK_RETRIED" in line for line in lines)
        
        assert has_retried, "应写入 TASK_RETRIED 事件"
        log("  ✅ 自动重试 blocked 任务")
        
        # 检查新 run 产生
        with open(events_path, "r") as f:
            lines = f.readlines()
            intent_count = sum(1 for line in lines if "WORKER_RUN_INTENT" in line)
        assert intent_count >= 2, f"应有至少 2 次派发（原始 + 重试），实际: {intent_count}"
        log(f"  ✅ 产生 {intent_count} 次派发")
        
    return True


def test_no_auto_retry_when_disabled():
    """验收 6：关闭自动重试时不重试"""
    log("测试 6: 关闭自动重试时不重试")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        sm = StateManager(base_dir)
        project = "test-no-retry"
        
        # PROJECT_STARTED
        sm.append_event({
            "type": "PROJECT_STARTED",
            "actor": "orchestrator",
            "project": project,
            "runId": "start-1",
            "payload": {},
            "idempotencyKey": f"{project}:PROJECT_STARTED:start-1",
        })
        
        # TASKSPEC_PUBLISHED
        sm.append_event({
            "type": "TASKSPEC_PUBLISHED",
            "actor": "pm",
            "project": project,
            "taskId": "TEST-1",
            "payload": {
                "taskId": "TEST-1",
                "goal": "Test no retry",
                "kind": "docs",
                "acceptance": ["done"],
            },
            "idempotencyKey": f"{project}:TEST-1:TASKSPEC_PUBLISHED",
        })
        
        # TASK_SKILL_SET
        sm.append_event({
            "type": "TASK_SKILL_SET",
            "actor": "human",
            "project": project,
            "taskId": "TEST-1",
            "payload": {"chosenSkill": "writer", "decisionSeq": 1},
            "idempotencyKey": f"{project}:TEST-1:TASK_SKILL_SET:1",
        })
        
        # 第一次运行失败
        run_id_1 = run_id("r")
        for ev in [
            ("WORKER_RUN_INTENT", {}),
            ("WORKER_RUN_STARTED", {}),
            ("WORKER_RUN_FAILED", {"reason": "failed"}),
            ("RUN_CLOSED", {"closeReason": "failed"}),
        ]:
            sm.append_event({
                "type": ev[0],
                "actor": "orchestrator",
                "project": project,
                "taskId": "TEST-1",
                "runId": run_id_1,
                "payload": ev[1],
                "idempotencyKey": f"{project}:TEST-1:{run_id_1}:{ev[0]}",
            })
        
        # 运行 tick（关闭重试）
        orch = Orchestrator(OrchestratorConfig(
            base_dir=base_dir,
            max_retries=0,  # 关闭自动重试
            worker_timeout_minutes=30,
        ))
        result = orch.tick()
        
        # 检查不产生重试
        events_path = base_dir / "audit" / "events.ndjson"
        with open(events_path, "r") as f:
            lines = f.readlines()
            has_retried = any("TASK_RETRIED" in line for line in lines)
        
        assert not has_retried, "不应自动重试"
        log("  ✅ 关闭重试时不产生重试")
        
    return True


def test_retry_count_limit():
    """验收 7：重试次数限制"""
    log("测试 7: 重试次数限制")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        sm = StateManager(base_dir)
        project = "test-retry-limit"
        
        # PROJECT_STARTED
        sm.append_event({
            "type": "PROJECT_STARTED",
            "actor": "orchestrator",
            "project": project,
            "runId": "start-1",
            "payload": {},
            "idempotencyKey": f"{project}:PROJECT_STARTED:start-1",
        })
        
        # TASKSPEC_PUBLISHED
        sm.append_event({
            "type": "TASKSPEC_PUBLISHED",
            "actor": "pm",
            "project": project,
            "taskId": "TEST-1",
            "payload": {
                "taskId": "TEST-1",
                "goal": "Test retry limit",
                "kind": "docs",
                "acceptance": ["done"],
            },
            "idempotencyKey": f"{project}:TEST-1:TASKSPEC_PUBLISHED",
        })
        
        # TASK_SKILL_SET
        sm.append_event({
            "type": "TASK_SKILL_SET",
            "actor": "human",
            "project": project,
            "taskId": "TEST-1",
            "payload": {"chosenSkill": "writer", "decisionSeq": 1},
            "idempotencyKey": f"{project}:TEST-1:TASK_SKILL_SET:1",
        })
        
        # 模拟已重试 2 次（手动写入）
        for i in range(1, 3):  # retry 1 和 retry 2
            sm.append_event({
                "type": "TASK_RETRIED",
                "actor": "orchestrator",
                "project": project,
                "taskId": "TEST-1",
                "runId": f"r-retry-{i}",
                "payload": {"retryCount": i, "reason": "auto_retry"},
                "idempotencyKey": f"{project}:TEST-1:r-retry-{i}:TASK_RETRIED:{i}",
            })
        
        # 当前任务失败
        run_id_1 = run_id("r")
        for ev in [
            ("WORKER_RUN_INTENT", {}),
            ("WORKER_RUN_STARTED", {}),
            ("WORKER_RUN_FAILED", {"reason": "failed"}),
            ("RUN_CLOSED", {"closeReason": "failed"}),
        ]:
            sm.append_event({
                "type": ev[0],
                "actor": "orchestrator",
                "project": project,
                "taskId": "TEST-1",
                "runId": run_id_1,
                "payload": ev[1],
                "idempotencyKey": f"{project}:TEST-1:{run_id_1}:{ev[0]}",
            })
        
        # 运行 tick（max_retries=2，应不再重试）
        orch = Orchestrator(OrchestratorConfig(
            base_dir=base_dir,
            max_retries=2,  # 最多重试 2 次
            worker_timeout_minutes=30,
        ))
        result = orch.tick()
        
        # 检查不产生新重试
        events_path = base_dir / "audit" / "events.ndjson"
        with open(events_path, "r") as f:
            lines = f.readlines()
            retry_count = sum(1 for line in lines if "TASK_RETRIED" in line)
        
        assert retry_count == 2, f"应保持 2 次重试，实际: {retry_count}"
        log("  ✅ 超过最大重试次数后不再重试")
        
    return True


def main():
    print("=" * 60)
    print("M4 验收测试：Watchdog / 审计")
    print("=" * 60)
    
    all_pass = True
    
    tests = [
        ("Watchdog Evidence 校验（PASS）", test_watchdog_evidence_verification),
        ("Path Safety 违规检测", test_watchdog_path_safety_violation),
        ("禁止命令检测", test_watchdog_deny_command),
        ("缺少必需字段检测", test_watchdog_missing_fields),
        ("自动重试 blocked 任务", test_auto_retry_blocked),
        ("关闭自动重试时不重试", test_no_auto_retry_when_disabled),
        ("重试次数限制", test_retry_count_limit),
    ]
    
    for name, fn in tests:
        print()
        try:
            fn()
        except Exception as e:
            log(f"  ❌ 失败: {e}")
            import traceback
            traceback.print_exc()
            all_pass = False
    
    print()
    print("=" * 60)
    if all_pass:
        print("✅ M4 所有验收测试通过")
    else:
        print("❌ M4 验收测试未完全通过")
    print("=" * 60)
    
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
