#!/usr/bin/env python3
"""
M2 验收测试：Orchestrator 自动派发
"""
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TOOL_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(TOOL_ROOT))

from core.state_manager import StateManager
from core.reducer import reduce_events
from core.orchestrator import Orchestrator, OrchestratorConfig
from core.ids import run_id


ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utc_now():
    return datetime.now(timezone.utc).strftime(ISO_FORMAT)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def test_dispatch_pending_tasks():
    """验收 1：自动派发 pending 任务"""
    log("测试 1: 自动派发 pending 任务")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        sm = StateManager(base_dir)
        project = "test-dispatch"
        
        # PROJECT_STARTED
        sm.append_event({
            "type": "PROJECT_STARTED",
            "actor": "orchestrator",
            "project": project,
            "runId": "start-1",
            "payload": {},
            "idempotencyKey": f"{project}:PROJECT_STARTED:start-1",
        })
        
        # TASKSPEC_PUBLISHED (taskId=DOCS-1, pending)
        sm.append_event({
            "type": "TASKSPEC_PUBLISHED",
            "actor": "pm",
            "project": project,
            "taskId": "DOCS-1",
            "payload": {
                "taskId": "DOCS-1",
                "goal": "Create docs",
                "kind": "docs",
                "acceptance": ["done"],
            },
            "idempotencyKey": f"{project}:DOCS-1:TASKSPEC_PUBLISHED",
        })
        
        # TASK_SKILL_SET (移除 awaiting_skill_decision gate)
        sm.append_event({
            "type": "TASK_SKILL_SET",
            "actor": "human",
            "project": project,
            "taskId": "DOCS-1",
            "payload": {"chosenSkill": "writer", "decisionSeq": 1},
            "idempotencyKey": f"{project}:DOCS-1:TASK_SKILL_SET:1",
        })
        
        # 运行 tick（应自动派发）
        orch = Orchestrator(OrchestratorConfig(base_dir=base_dir))
        result = orch.tick()
        
        # 检查是否产生 WORKER_RUN_INTENT + STARTED
        events_path = base_dir / "audit" / "events.ndjson"
        with open(events_path, "r") as f:
            lines = f.readlines()
            has_intent = any("WORKER_RUN_INTENT" in line for line in lines)
            has_started = any("WORKER_RUN_STARTED" in line for line in lines)
        
        assert has_intent, "应写入 WORKER_RUN_INTENT 事件"
        assert has_started, "应写入 WORKER_RUN_STARTED 事件"
        log("  ✅ 自动派发 pending 任务")
        
        # 检查状态
        status = result.status
        task = status["tasks"][0]
        assert task["state"] == "running", f"任务应变为 running，实际: {task['state']}"
        assert task["runId"] is not None, "任务应有 runId"
        log(f"  ✅ 任务状态变为 running，runId={task['runId']}")
        
    return True


def test_no_dispatch_blocked_tasks():
    """验收 2：不派发有 gates 阻塞的任务"""
    log("测试 2: 不派发有 gates 阻塞的任务")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        sm = StateManager(base_dir)
        project = "test-blocked"
        
        # PROJECT_STARTED
        sm.append_event({
            "type": "PROJECT_STARTED",
            "actor": "orchestrator",
            "project": project,
            "runId": "start-1",
            "payload": {},
            "idempotencyKey": f"{project}:PROJECT_STARTED:start-1",
        })
        
        # TASKSPEC_PUBLISHED (taskId=DOCS-1)
        sm.append_event({
            "type": "TASKSPEC_PUBLISHED",
            "actor": "pm",
            "project": project,
            "taskId": "DOCS-1",
            "payload": {
                "taskId": "DOCS-1",
                "goal": "Create docs",
                "kind": "docs",
                "acceptance": ["done"],
            },
            "idempotencyKey": f"{project}:DOCS-1:TASKSPEC_PUBLISHED",
        })
        
        # 注意：不给 TASK_SKILL_SET，保留 awaiting_skill_decision gate
        
        # 运行 tick（不应派发）
        orch = Orchestrator(OrchestratorConfig(base_dir=base_dir))
        result = orch.tick()
        
        # 检查是否产生派发事件
        events_path = base_dir / "audit" / "events.ndjson"
        with open(events_path, "r") as f:
            lines = f.readlines()
            has_intent = any("WORKER_RUN_INTENT" in line for line in lines)
        
        assert not has_intent, "有 gate 阻塞时不应派发"
        log("  ✅ 有 gates 阻塞时不予派发")
        
        # 检查任务仍在 pending
        status = result.status
        task = status["tasks"][0]
        assert task["state"] == "pending", f"任务应保持 pending，实际: {task['state']}"
        assert "awaiting_skill_decision" in task["gates"], "应有 awaiting_skill_decision gate"
        log(f"  ✅ 任务保持 pending，gates={task['gates']}")
        
    return True


def test_worker_timeout():
    """验收 3：Worker 超时检测"""
    log("测试 3: Worker 超时检测")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        sm = StateManager(base_dir)
        project = "test-timeout"
        
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
            "taskId": "DOCS-1",
            "payload": {
                "taskId": "DOCS-1",
                "goal": "Create docs",
                "kind": "docs",
                "acceptance": ["done"],
            },
            "idempotencyKey": f"{project}:DOCS-1:TASKSPEC_PUBLISHED",
        })
        
        # TASK_SKILL_SET
        sm.append_event({
            "type": "TASK_SKILL_SET",
            "actor": "human",
            "project": project,
            "taskId": "DOCS-1",
            "payload": {"chosenSkill": "writer", "decisionSeq": 1},
            "idempotencyKey": f"{project}:DOCS-1:TASK_SKILL_SET:1",
        })
        
        # WORKER_RUN_INTENT + STARTED（时间设置为 31 分钟前）
        old_time = datetime.now(timezone.utc) - timedelta(minutes=31)
        run_id_val = run_id("r")
        
        sm.append_event({
            "type": "WORKER_RUN_INTENT",
            "actor": "orchestrator",
            "project": project,
            "taskId": "DOCS-1",
            "runId": run_id_val,
            "payload": {"reason": "test"},
            "idempotencyKey": f"{project}:DOCS-1:{run_id_val}:WORKER_RUN_INTENT",
        })
        
        sm.append_event({
            "type": "WORKER_RUN_STARTED",
            "actor": "orchestrator",
            "project": project,
            "taskId": "DOCS-1",
            "runId": run_id_val,
            "payload": {},
            "idempotencyKey": f"{project}:DOCS-1:{run_id_val}:WORKER_RUN_STARTED",
            "at": old_time.strftime(ISO_FORMAT),
        })
        
        # 运行 tick（应检测超时）
        orch = Orchestrator(OrchestratorConfig(
            base_dir=base_dir,
            worker_timeout_minutes=30  # 30 分钟超时
        ))
        result = orch.tick()
        
        # 检查是否写入超时事件
        events_path = base_dir / "audit" / "events.ndjson"
        with open(events_path, "r") as f:
            lines = f.readlines()
            has_failed = any("WORKER_RUN_FAILED" in line and "timeout" in line for line in lines)
            has_closed = any("RUN_CLOSED" in line and "timeout" in line for line in lines)
        
        assert has_failed, "应写入 WORKER_RUN_FAILED(timeout)"
        assert has_closed, "应写入 RUN_CLOSED(timeout)"
        log("  ✅ 检测到 Worker 超时，触发失败关闭")
        
        # 检查状态
        status = result.status
        task = status["tasks"][0]
        assert task["state"] == "blocked", f"任务应变为 blocked，实际: {task['state']}"
        log(f"  ✅ 任务状态变为 blocked")
        
    return True


def test_no_repeated_dispatch():
    """验收 4：重复派发防护"""
    log("测试 4: 重复派发防护")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        sm = StateManager(base_dir)
        project = "test-no-repeat"
        
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
            "taskId": "DOCS-1",
            "payload": {
                "taskId": "DOCS-1",
                "goal": "Create docs",
                "kind": "docs",
                "acceptance": ["done"],
            },
            "idempotencyKey": f"{project}:DOCS-1:TASKSPEC_PUBLISHED",
        })
        
        # TASK_SKILL_SET
        sm.append_event({
            "type": "TASK_SKILL_SET",
            "actor": "human",
            "project": project,
            "taskId": "DOCS-1",
            "payload": {"chosenSkill": "writer", "decisionSeq": 1},
            "idempotencyKey": f"{project}:DOCS-1:TASK_SKILL_SET:1",
        })
        
        # 手动派发一次
        run_id_val = run_id("r")
        sm.append_event({
            "type": "WORKER_RUN_INTENT",
            "actor": "orchestrator",
            "project": project,
            "taskId": "DOCS-1",
            "runId": run_id_val,
            "payload": {"reason": "manual"},
            "idempotencyKey": f"{project}:DOCS-1:{run_id_val}:WORKER_RUN_INTENT",
        })
        
        # 多次运行 tick
        orch = Orchestrator(OrchestratorConfig(base_dir=base_dir))
        orch.tick()
        orch.tick()
        orch.tick()
        
        # 检查派发次数
        events_path = base_dir / "audit" / "events.ndjson"
        with open(events_path, "r") as f:
            lines = f.readlines()
            intent_count = sum(1 for line in lines if "WORKER_RUN_INTENT" in line)
        
        # 第一次是手动派发，后续 tick 不应产生新派发（因为已有 open run）
        assert intent_count == 1, f"只应有 1 次派发，实际: {intent_count}"
        log("  ✅ 重复 tick 不产生新派发")
        
    return True


def main():
    print("=" * 60)
    print("M2 验收测试：Orchestrator 自动派发")
    print("=" * 60)
    
    all_pass = True
    
    tests = [
        ("自动派发 pending 任务", test_dispatch_pending_tasks),
        ("不派发有 gates 阻塞的任务", test_no_dispatch_blocked_tasks),
        ("Worker 超时检测", test_worker_timeout),
        ("重复派发防护", test_no_repeated_dispatch),
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
        print("✅ M2 所有验收测试通过")
    else:
        print("❌ M2 验收测试未完全通过")
    print("=" * 60)
    
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
