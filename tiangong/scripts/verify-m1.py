#!/usr/bin/env python3
"""
M1 验收测试：state_manager 基础设施
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TOOL_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(TOOL_ROOT))

from core.state_manager import StateManager, FileLock, LockTimeout
from core.reducer import reduce_events
from core.protocol import compute_crc32, verify_crc32


ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utc_now():
    return datetime.now(timezone.utc).strftime(ISO_FORMAT)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def test_crc32():
    """验收 1：CRC32 校验"""
    log("测试 1: CRC32 校验")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        sm = StateManager(base_dir)
        
        # 写入合法事件
        event = {
            "type": "TEST_EVENT",
            "actor": "test",
            "project": "test",
            "payload": {"data": "test"},
            "idempotencyKey": "test:crc:test:1",
        }
        result = sm.append_event(event)
        assert result["status"] == "appended", "事件应写入成功"
        
        # 读取并验证
        events_path = base_dir / "audit" / "events.ndjson"
        with open(events_path, "r") as f:
            line = f.readline()
            saved_event = json.loads(line)
        
        assert verify_crc32(saved_event), "CRC32 验证应通过"
        log("  ✅ CRC32 校验通过")
        
        # 测试篡改检测
        corrupted = {**saved_event, "payload": "tampered"}
        assert not verify_crc32(corrupted), "篡改后 CRC32 应失败"
        log("  ✅ 篡改检测通过")
        
    return True


def test_truncated_recovery():
    """验收 2：事件截断恢复"""
    log("测试 2: 事件截断恢复")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        
        # 先写入几个正常事件
        sm = StateManager(base_dir)
        for i in range(3):
            sm.append_event({
                "type": "TEST_EVENT",
                "actor": "test",
                "project": "test",
                "payload": {"seq": i},
                "idempotencyKey": f"test:trunc:{i}",
            })
        
        events_path = base_dir / "audit" / "events.ndjson"
        
        # 截断文件（模拟崩溃）
        with open(events_path, "r+") as f:
            content = f.read()
            # 只保留前 2 行的一半
            lines = content.split("\n")
            truncated = lines[0] + "\n" + lines[1][:50]  # 不完整的行
            f.seek(0)
            f.write(truncated)
            f.truncate()
        
        # 通过 orchestrator tick 触发恢复逻辑
        from core.orchestrator import Orchestrator, OrchestratorConfig
        orch = Orchestrator(OrchestratorConfig(base_dir=base_dir))
        orch.tick()
        
        # 检查是否写入 RECOVERY_STARTED
        with open(events_path, "r") as f:
            lines = f.readlines()
            has_recovery = any("RECOVERY_STARTED" in line for line in lines)
            assert has_recovery, "应写入 RECOVERY_STARTED 事件"
            log("  ✅ 写入 RECOVERY_STARTED 事件")
        
        # 检查 degraded 模式
        status = json.loads((base_dir / "status.json").read_text())
        assert status["project"]["mode"] == "degraded", "应进入 degraded 模式"
        assert status["project"]["degradedReason"] == "recovery_in_progress", "degraded 原因应为 recovery_in_progress"
        log("  ✅ 进入 degraded_mode")
        
    return True


def test_lock_timeout():
    """验收 3：锁超时检测"""
    log("测试 3: 锁超时检测")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_path = Path(tmpdir) / "test.lock"
        
        # 创建锁文件
        lock = FileLock(lock_path, timeout=0.5, poll_interval=0.05)
        lock.acquire()
        
        # 另一个进程尝试获取锁（使用 subprocess 模拟）
        script = f'''
import sys
sys.path.insert(0, "{TOOL_ROOT}")
from pathlib import Path
from core.state_manager import FileLock, LockTimeout
try:
    with FileLock(Path("{lock_path}"), timeout=0.2, poll_interval=0.02):
        print("LOCK_ACQUIRED")
except LockTimeout:
    print("TIMEOUT")
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=TOOL_ROOT
        )
        
        assert "TIMEOUT" in result.stdout, "应触发锁超时"
        log("  ✅ 锁超时检测通过")
        
        lock.release()
        
    return True


def test_watchdog_heartbeat():
    """验收 4：Watchdog 心跳超时"""
    log("测试 4: Watchdog 心跳超时")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        
        project = "test-heartbeat"
        
        # 先写入 PROJECT_STARTED
        sm = StateManager(base_dir)
        sm.append_event({
            "type": "PROJECT_STARTED",
            "actor": "orchestrator",
            "project": project,
            "runId": "start-1",
            "payload": {},
            "idempotencyKey": f"{project}:PROJECT_STARTED:start-1",
        })
        
        # 写入一个 5 分钟前的心跳（模拟失联）
        from datetime import timedelta
        old_time = datetime.now(timezone.utc) - timedelta(seconds=400)
        old_time_str = old_time.strftime(ISO_FORMAT)
        
        sm.append_event({
            "type": "WATCHDOG_HEARTBEAT",
            "actor": "watchdog",
            "project": project,
            "payload": {},
            "idempotencyKey": f"{project}:WATCHDOG_HEARTBEAT:99999",  # 用未来 window 确保不幂等掉
            "at": old_time_str,
        })
        
        # 运行 orchestrator tick
        from core.orchestrator import Orchestrator, OrchestratorConfig
        
        orch = Orchestrator(OrchestratorConfig(base_dir=base_dir, heartbeat_timeout_sec=180))
        orch.tick()
        
        # 检查是否写入 WATCHDOG_UNRESPONSIVE
        events_path = base_dir / "audit" / "events.ndjson"
        with open(events_path, "r") as f:
            lines = f.readlines()
            has_unresponsive = any("WATCHDOG_UNRESPONSIVE" in line for line in lines)
            assert has_unresponsive, "应写入 WATCHDOG_UNRESPONSIVE 事件"
            log("  ✅ 写入 WATCHDOG_UNRESPONSIVE 事件")
        
        # 检查 degraded 模式
        status = json.loads((base_dir / "status.json").read_text())
        assert status["project"]["mode"] == "degraded", "应进入 degraded 模式"
        assert status["project"]["degradedReason"] == "watchdog_unresponsive", "degraded 原因应为 watchdog_unresponsive"
        log("  ✅ 进入 degraded_mode")
        
    return True


def test_idempotency():
    """测试幂等性"""
    log("测试: 幂等性")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        sm = StateManager(base_dir)
        
        key = "test:idempotency:1"
        
        # 第一次写入
        result1 = sm.append_event({
            "type": "TEST_EVENT",
            "actor": "test",
            "project": "test",
            "payload": {},
            "idempotencyKey": key,
        })
        assert result1["status"] == "appended", "第一次应写入"
        
        # 第二次写入（同 key）
        result2 = sm.append_event({
            "type": "TEST_EVENT",
            "actor": "test",
            "project": "test",
            "payload": {"data": "different"},
            "idempotencyKey": key,
        })
        assert result2["status"] == "deduped", "第二次应去重"
        
        events_path = base_dir / "audit" / "events.ndjson"
        with open(events_path, "r") as f:
            lines = [l for l in f.readlines() if l.strip()]
            assert len(lines) == 1, "只应有一条事件"
        
        log("  ✅ 幂等性通过")
        
    return True


def main():
    print("=" * 60)
    print("M1 验收测试：state_manager 基础设施")
    print("=" * 60)
    
    all_pass = True
    
    tests = [
        ("CRC32 校验", test_crc32),
        ("事件截断恢复", test_truncated_recovery),
        ("锁超时检测", test_lock_timeout),
        ("Watchdog 心跳超时", test_watchdog_heartbeat),
        ("幂等性", test_idempotency),
    ]
    
    for name, fn in tests:
        print()
        try:
            fn()
        except Exception as e:
            log(f"  ❌ 失败: {e}")
            all_pass = False
    
    print()
    print("=" * 60)
    if all_pass:
        print("✅ M1 所有验收测试通过")
    else:
        print("❌ M1 验收测试未完全通过")
    print("=" * 60)
    
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
