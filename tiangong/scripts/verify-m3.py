#!/usr/bin/env python3
"""
M3 验收测试：结果消费
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
from core.ids import run_id


ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utc_now():
    return datetime.now(timezone.utc).strftime(ISO_FORMAT)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def test_result_aggregation():
    """验收 1：结果聚合（done 任务）"""
    log("测试 1: 结果聚合（done 任务）")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        sm = StateManager(base_dir)
        project = "test-aggregation"
        
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
        
        # WORKER_RUN_INTENT + STARTED + COMPLETED
        run_id_val = run_id("r")
        sm.append_event({
            "type": "WORKER_RUN_INTENT",
            "actor": "orchestrator",
            "project": project,
            "taskId": "DOCS-1",
            "runId": run_id_val,
            "payload": {},
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
        })
        sm.append_event({
            "type": "WORKER_RUN_COMPLETED",
            "actor": "orchestrator",
            "project": project,
            "taskId": "DOCS-1",
            "runId": run_id_val,
            "payload": {"result": "success"},
            "idempotencyKey": f"{project}:DOCS-1:{run_id_val}:WORKER_RUN_COMPLETED",
        })
        
        # EVIDENCE_SUBMITTED
        sm.append_event({
            "type": "EVIDENCE_SUBMITTED",
            "actor": "orchestrator",
            "project": project,
            "taskId": "DOCS-1",
            "runId": run_id_val,
            "payload": {
                "evidencePath": f"evidence/{run_id_val}.md",
                "patchPath": f"evidence/{run_id_val}.patch",
            },
            "idempotencyKey": f"{project}:DOCS-1:{run_id_val}:EVIDENCE_SUBMITTED",
        })
        
        # WATCHDOG_VERDICT PASS
        sm.append_event({
            "type": "WATCHDOG_VERDICT",
            "actor": "watchdog",
            "project": project,
            "taskId": "DOCS-1",
            "runId": run_id_val,
            "payload": {"verdict": "PASS", "reasons": []},
            "idempotencyKey": f"{project}:DOCS-1:{run_id_val}:WATCHDOG_VERDICT",
        })
        
        # RUN_CLOSED
        sm.append_event({
            "type": "RUN_CLOSED",
            "actor": "orchestrator",
            "project": project,
            "taskId": "DOCS-1",
            "runId": run_id_val,
            "payload": {"closeReason": "completed_with_pass"},
            "idempotencyKey": f"{project}:DOCS-1:{run_id_val}:RUN_CLOSED",
        })
        
        # 运行 tick
        orch = Orchestrator(OrchestratorConfig(base_dir=base_dir))
        result = orch.tick()
        
        # 检查结果聚合 - done 任务没有 state 字段（状态分片优化）
        status = result.status
        task = status["tasks"][0]
        # done 任务：没有 state 字段，有 lastRunId
        assert "state" not in task, f"done 任务不应有 state 字段，实际: {task}"
        assert task.get("lastRunId") == run_id_val, f"应有 lastRunId"
        assert task.get("evidencePath") is not None, f"应有 evidencePath"
        log("  ✅ done 任务结果聚合正确（状态分片优化）")
        
        # 检查 RESULT_NOTIFIED 事件
        events_path = base_dir / "audit" / "events.ndjson"
        with open(events_path, "r") as f:
            lines = f.readlines()
            has_notified = any("RESULT_NOTIFIED" in line for line in lines)
        assert has_notified, "应写入 RESULT_NOTIFIED 事件"
        log("  ✅ 写入 RESULT_NOTIFIED 事件")
        
    return True


def test_blocked_result():
    """验收 2：blocked 结果通知"""
    log("测试 2: blocked 结果通知")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        sm = StateManager(base_dir)
        project = "test-blocked-result"
        
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
            "taskId": "CODE-1",
            "payload": {
                "taskId": "CODE-1",
                "goal": "Fix bug",
                "kind": "coding",
                "acceptance": ["done"],
            },
            "idempotencyKey": f"{project}:CODE-1:TASKSPEC_PUBLISHED",
        })
        
        # TASK_SKILL_SET
        sm.append_event({
            "type": "TASK_SKILL_SET",
            "actor": "human",
            "project": project,
            "taskId": "CODE-1",
            "payload": {"chosenSkill": "superpower", "decisionSeq": 1},
            "idempotencyKey": f"{project}:CODE-1:TASK_SKILL_SET:1",
        })
        
        # WORKER_RUN_INTENT + STARTED + FAILED
        run_id_val = run_id("r")
        sm.append_event({
            "type": "WORKER_RUN_INTENT",
            "actor": "orchestrator",
            "project": project,
            "taskId": "CODE-1",
            "runId": run_id_val,
            "payload": {},
            "idempotencyKey": f"{project}:CODE-1:{run_id_val}:WORKER_RUN_INTENT",
        })
        sm.append_event({
            "type": "WORKER_RUN_STARTED",
            "actor": "orchestrator",
            "project": project,
            "taskId": "CODE-1",
            "runId": run_id_val,
            "payload": {},
            "idempotencyKey": f"{project}:CODE-1:{run_id_val}:WORKER_RUN_STARTED",
        })
        sm.append_event({
            "type": "WORKER_RUN_FAILED",
            "actor": "orchestrator",
            "project": project,
            "taskId": "CODE-1",
            "runId": run_id_val,
            "payload": {"reason": "build failed"},
            "idempotencyKey": f"{project}:CODE-1:{run_id_val}:WORKER_RUN_FAILED",
        })
        
        # RUN_CLOSED
        sm.append_event({
            "type": "RUN_CLOSED",
            "actor": "orchestrator",
            "project": project,
            "taskId": "CODE-1",
            "runId": run_id_val,
            "payload": {"closeReason": "failed"},
            "idempotencyKey": f"{project}:CODE-1:{run_id_val}:RUN_CLOSED",
        })
        
        # 运行 tick
        orch = Orchestrator(OrchestratorConfig(base_dir=base_dir))
        result = orch.tick()
        
        # 检查 blocked 状态
        status = result.status
        task = status["tasks"][0]
        assert task["state"] == "blocked", f"任务应为 blocked，实际: {task['state']}"
        assert task["result"].get("failureReason") == "build failed", f"失败原因应为 build failed"
        log("  ✅ blocked 任务结果聚合正确")
        
        # 检查 RESULT_NOTIFIED 事件
        events_path = base_dir / "audit" / "events.ndjson"
        with open(events_path, "r") as f:
            lines = f.readlines()
            has_notified = any("RESULT_NOTIFIED" in line and "失败" in line for line in lines)
        assert has_notified, "应写入包含失败信息的 RESULT_NOTIFIED 事件"
        log("  ✅ 写入 blocked 通知事件")
        
    return True


def test_notification_idempotency():
    """验收 3：通知幂等（不重复通知）"""
    log("测试 3: 通知幂等")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        sm = StateManager(base_dir)
        project = "test-notify-idempotent"
        
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
                "goal": "Test idempotency",
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
        
        # 完成整个流程
        run_id_val = run_id("r")
        for ev_type, ev_data in [
            ("WORKER_RUN_INTENT", {"reason": "test"}),
            ("WORKER_RUN_STARTED", {}),
            ("WORKER_RUN_COMPLETED", {"result": "success"}),
            ("EVIDENCE_SUBMITTED", {"evidencePath": "x", "patchPath": "y"}),
            ("WATCHDOG_VERDICT", {"verdict": "PASS", "reasons": []}),
            ("RUN_CLOSED", {"closeReason": "completed_with_pass"}),
        ]:
            sm.append_event({
                "type": ev_type,
                "actor": "orchestrator",
                "project": project,
                "taskId": "TEST-1",
                "runId": run_id_val,
                "payload": ev_data,
                "idempotencyKey": f"{project}:TEST-1:{run_id_val}:{ev_type}",
            })
        
        # 运行第一次 tick
        orch = Orchestrator(OrchestratorConfig(base_dir=base_dir))
        orch.tick()
        
        # 检查通知次数
        events_path = base_dir / "audit" / "events.ndjson"
        with open(events_path, "r") as f:
            lines = f.readlines()
            notify_count = sum(1 for line in lines if "RESULT_NOTIFIED" in line)
        assert notify_count == 1, f"应有 1 次通知，实际: {notify_count}"
        log("  ✅ 首次 tick 产生 1 次通知")
        
        # 再次运行 tick（不应产生新通知）
        orch.tick()
        
        with open(events_path, "r") as f:
            lines = f.readlines()
            notify_count2 = sum(1 for line in lines if "RESULT_NOTIFIED" in line)
        assert notify_count2 == 1, f"再次 tick 后应有 1 次通知，实际: {notify_count2}"
        log("  ✅ 再次 tick 不产生重复通知")
        
    return True


def test_multiple_tasks_results():
    """验收 4：多个任务结果处理"""
    log("测试 4: 多个任务结果处理")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        sm = StateManager(base_dir)
        project = "test-multi-results"
        
        # PROJECT_STARTED
        sm.append_event({
            "type": "PROJECT_STARTED",
            "actor": "orchestrator",
            "project": project,
            "runId": "start-1",
            "payload": {},
            "idempotencyKey": f"{project}:PROJECT_STARTED:start-1",
        })
        
        # 创建 3 个任务
        for i, (task_id, goal) in enumerate([("T1", "Task 1"), ("T2", "Task 2"), ("T3", "Task 3")]):
            # TASKSPEC_PUBLISHED
            sm.append_event({
                "type": "TASKSPEC_PUBLISHED",
                "actor": "pm",
                "project": project,
                "taskId": task_id,
                "payload": {
                    "taskId": task_id,
                    "goal": goal,
                    "kind": "docs",
                    "acceptance": ["done"],
                },
                "idempotencyKey": f"{project}:{task_id}:TASKSPEC_PUBLISHED",
            })
            
            # TASK_SKILL_SET
            sm.append_event({
                "type": "TASK_SKILL_SET",
                "actor": "human",
                "project": project,
                "taskId": task_id,
                "payload": {"chosenSkill": "writer", "decisionSeq": 1},
                "idempotencyKey": f"{project}:{task_id}:TASK_SKILL_SET:1",
            })
            
            # 完成 T1 和 T3，T2 失败
            if i != 1:
                run_id_val = run_id("r")
                for ev_type, ev_data in [
                    ("WORKER_RUN_INTENT", {}),
                    ("WORKER_RUN_STARTED", {}),
                    ("WORKER_RUN_COMPLETED", {"result": "success"}),
                    ("EVIDENCE_SUBMITTED", {"evidencePath": "x"}),
                    ("WATCHDOG_VERDICT", {"verdict": "PASS", "reasons": []}),
                    ("RUN_CLOSED", {"closeReason": "completed_with_pass"}),
                ]:
                    sm.append_event({
                        "type": ev_type,
                        "actor": "orchestrator",
                        "project": project,
                        "taskId": task_id,
                        "runId": run_id_val,
                        "payload": ev_data,
                        "idempotencyKey": f"{project}:{task_id}:{run_id_val}:{ev_type}",
                    })
            else:
                run_id_val = run_id("r")
                for ev_type, ev_data in [
                    ("WORKER_RUN_INTENT", {}),
                    ("WORKER_RUN_STARTED", {}),
                    ("WORKER_RUN_FAILED", {"reason": "test failure"}),
                    ("RUN_CLOSED", {"closeReason": "failed"}),
                ]:
                    sm.append_event({
                        "type": ev_type,
                        "actor": "orchestrator",
                        "project": project,
                        "taskId": task_id,
                        "runId": run_id_val,
                        "payload": ev_data,
                        "idempotencyKey": f"{project}:{task_id}:{run_id_val}:{ev_type}",
                    })
        
        # 运行 tick
        orch = Orchestrator(OrchestratorConfig(base_dir=base_dir))
        result = orch.tick()
        
        # 检查状态 - done 任务没有 state，blocked 有 state
        status = result.status
        tasks = status["tasks"]
        done_count = sum(1 for t in tasks if "state" not in t)  # done 任务没有 state
        blocked_count = sum(1 for t in tasks if t.get("state") == "blocked")
        
        assert done_count == 2, f"应有 2 个 done，实际: {done_count}"
        assert blocked_count == 1, f"应有 1 个 blocked，实际: {blocked_count}"
        log(f"  ✅ 2 个 done，1 个 blocked")
        
        # 检查通知次数
        events_path = base_dir / "audit" / "events.ndjson"
        with open(events_path, "r") as f:
            lines = f.readlines()
            notify_count = sum(1 for line in lines if "RESULT_NOTIFIED" in line)
        assert notify_count == 3, f"应有 3 次通知（2 done + 1 blocked），实际: {notify_count}"
        log("  ✅ 3 次通知正确发送")
        
    return True


def main():
    print("=" * 60)
    print("M3 验收测试：结果消费")
    print("=" * 60)
    
    all_pass = True
    
    tests = [
        ("结果聚合（done）", test_result_aggregation),
        ("blocked 结果通知", test_blocked_result),
        ("通知幂等", test_notification_idempotency),
        ("多个任务结果", test_multiple_tasks_results),
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
        print("✅ M3 所有验收测试通过")
    else:
        print("❌ M3 验收测试未完全通过")
    print("=" * 60)
    
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
