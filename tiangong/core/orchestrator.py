import json
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .reducer import reduce_events, read_events
from .state_manager import StateManager
from .skill_registry import SkillRegistry
from .skill_router import SkillRouter
from .ids import run_id as run_id_gen
from .openclaw_client import OpenClawClient

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


@dataclass
class OrchestratorConfig:
    base_dir: Path
    heartbeat_timeout_sec: int = 180
    worker_timeout_minutes: int = 30  # Worker 超时时间
    max_retries: int = 3  # 最大重试次数
    retry_delay_seconds: int = 60  # 重试间隔（秒）


class Orchestrator:
    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.base_dir = Path(config.base_dir)
        self.team = self._load_team()
        self.project = self.team.get("project", "unknown")
        self.labels = self.team.get("labels", {})
        self.session_label = self.labels.get("orchestrator")
        self.sm = StateManager(self.base_dir)

    def _load_team(self) -> dict:
        team_path = self.base_dir / "team.json"
        if team_path.exists():
            try:
                return json.loads(team_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _parse_time(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.strptime(value, ISO_FORMAT).replace(tzinfo=timezone.utc)
        except Exception:
            return None

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _build_event(self, *, etype: str, task_id: Optional[str], run_id_str: Optional[str], payload: dict,
                     idempotency_key: str, causation_id: Optional[str] = None) -> dict:
        event = {
            "type": etype,
            "actor": "orchestrator",
            "project": self.project,
            "payload": payload,
            "idempotencyKey": idempotency_key,
        }
        if task_id:
            event["taskId"] = task_id
        if run_id_str:
            event["runId"] = run_id_str
            event["correlationId"] = run_id_str
        if self.session_label:
            event["sessionLabel"] = self.session_label
        if causation_id:
            event["causationId"] = causation_id
        return event

    def _event_index(self, events: List[dict]) -> Tuple[set, set, set, dict]:
        halted_by_verdict = set()
        aborted = set()
        closed = set()
        last_heartbeat_at = None
        last_project_started_at = None
        last_project_resumed_at = None
        last_project_halted_at = None
        last_project_finished_at = None

        for ev in events:
            etype = ev.get("type")
            ts = self._parse_time(ev.get("at"))
            if etype == "PROJECT_HALTED":
                verdict_id = ev.get("causationId") or ev.get("payload", {}).get("verdictEventId")
                if verdict_id:
                    halted_by_verdict.add(verdict_id)
                if ts and (last_project_halted_at is None or ts > last_project_halted_at):
                    last_project_halted_at = ts
            elif etype == "WORKER_RUN_ABORTED":
                aborted.add((ev.get("taskId"), ev.get("runId")))
            elif etype == "RUN_CLOSED":
                closed.add((ev.get("taskId"), ev.get("runId")))
            elif etype == "WATCHDOG_HEARTBEAT":
                if ts and (last_heartbeat_at is None or ts > last_heartbeat_at):
                    last_heartbeat_at = ts
            elif etype == "PROJECT_STARTED":
                if ts and (last_project_started_at is None or ts > last_project_started_at):
                    last_project_started_at = ts
            elif etype == "PROJECT_RESUMED":
                if ts and (last_project_resumed_at is None or ts > last_project_resumed_at):
                    last_project_resumed_at = ts
            elif etype == "PROJECT_FINISHED":
                if ts and (last_project_finished_at is None or ts > last_project_finished_at):
                    last_project_finished_at = ts

        return halted_by_verdict, aborted, closed, {
            "last_heartbeat_at": last_heartbeat_at,
            "last_project_started_at": last_project_started_at,
            "last_project_resumed_at": last_project_resumed_at,
            "last_project_halted_at": last_project_halted_at,
            "last_project_finished_at": last_project_finished_at,
        }

    def _enforce_block_sequence(self, events: List[dict]):
        halted_by_verdict, aborted, closed, _ = self._event_index(events)
        for ev in events:
            if ev.get("type") != "WATCHDOG_VERDICT":
                continue
            payload = ev.get("payload", {}) or {}
            if payload.get("verdict") != "BLOCK":
                continue
            verdict_id = ev.get("eventId")
            task_id = ev.get("taskId")
            run_id_val = ev.get("runId")
            if not verdict_id or not task_id or not run_id:
                continue

            if verdict_id not in halted_by_verdict:
                halt_event = self._build_event(
                    etype="PROJECT_HALTED",
                    task_id=task_id,
                    run_id_str=run_id_val,
                    payload={"haltReason": "blocked_by_watchdog", "verdictEventId": verdict_id},
                    causation_id=verdict_id,
                    idempotency_key=f"{self.project}:{task_id}:{run_id}:PROJECT_HALTED:{verdict_id}",
                )
                self.sm.append_event(halt_event)

            if (task_id, run_id_val) not in aborted:
                abort_event = self._build_event(
                    etype="WORKER_RUN_ABORTED",
                    task_id=task_id,
                    run_id_str=run_id_val,
                    payload={"reason": "blocked_by_watchdog"},
                    causation_id=verdict_id,
                    idempotency_key=f"{self.project}:{task_id}:{run_id}:WORKER_RUN_ABORTED",
                )
                self.sm.append_event(abort_event)

            if (task_id, run_id_val) not in closed:
                close_event = self._build_event(
                    etype="RUN_CLOSED",
                    task_id=task_id,
                    run_id_str=run_id_val,
                    payload={"closeReason": "blocked_by_watchdog", "verdictEventId": verdict_id},
                    causation_id=verdict_id,
                    idempotency_key=f"{self.project}:{task_id}:{run_id_val}:RUN_CLOSED",
                )
                self.sm.append_event(close_event)

    def _watchdog_heartbeat(self, events: List[dict]):
        _, _, _, info = self._event_index(events)
        last_heartbeat = info.get("last_heartbeat_at")
        last_started = info.get("last_project_started_at")
        last_resumed = info.get("last_project_resumed_at")
        last_halted = info.get("last_project_halted_at")
        last_finished = info.get("last_project_finished_at")

        # Skip heartbeat checks when project is halted or finished.
        if last_finished and (not last_started or last_finished > last_started):
            return
        if last_halted and (not last_resumed or last_halted > last_resumed):
            return

        # Watchdog heartbeat timeout should only check last_heartbeat, not started/resumed
        # Using started/resumed would mask watchdog inactivity after project restart
        if not last_heartbeat:
            return

        now = self._now()
        delta = (now - last_heartbeat).total_seconds()
        if delta < self.config.heartbeat_timeout_sec:
            return

        window = int(now.timestamp() // self.config.heartbeat_timeout_sec)
        idempotency_key = f"{self.project}:WATCHDOG_UNRESPONSIVE:{window}"
        unresp_event = self._build_event(
            etype="WATCHDOG_UNRESPONSIVE",
            task_id=None,
            run_id=None,
            payload={"lastHeartbeatAt": last_heartbeat.strftime(ISO_FORMAT)},
            idempotency_key=idempotency_key,
        )
        self.sm.append_event(unresp_event)

    def _dispatch_pending_tasks(self, status: dict):
        """自动派发 pending 任务（无 gates 阻塞）"""
        if status.get("project", {}).get("halted"):
            return  # 项目已 halt，不派发

        project_locks = status.get("locks", {}).get("project")
        if project_locks == "running":
            pass  # 允许并行派发

        tasks = status.get("tasks", [])
        dispatched = 0

        # 获取当前所有 tasks 的最后 runId 列表
        last_runs = {}
        for t in status.get("tasks", []):
            task_id = t.get("taskId")
            last_run_id = t.get("runId") or t.get("lastRunId")
            if last_run_id:
                last_runs[task_id] = last_run_id

        for task in tasks:
            task_id = task.get("taskId")
            state = task.get("state")
            gates = task.get("gates", [])

            # 只派发 pending 状态且无阻塞 gates 的任务
            if state != "pending":
                continue
            if gates:  # 有 gate 阻塞（如 awaiting_skill_decision）
                continue

            # 检查是否已有 open run（基于 tasks 中的状态，不是 locks）
            if task_id in last_runs:
                # 如果有 runId，检查是否已经关闭
                # 如果状态不是 running，说明 run 已经关闭，可以派发新任务
                if task.get("state") in ("running", "assigned"):
                    continue  # 仍在运行，不派发
                # 否则 state 是 None/done/pending，允许派发
                pass

            # 生成新 runId 并派发
            run_id_val = run_id_gen("r")
            intent_event = self._build_event(
                etype="WORKER_RUN_INTENT",
                task_id=task_id,
                run_id_str=run_id_val,
                payload={"reason": "auto_dispatch"},
                causation_id=None,
                idempotency_key=f"{self.project}:{task_id}:{run_id_val}:WORKER_RUN_INTENT",
            )
            self.sm.append_event(intent_event)

            # Spawn Worker（异步模式）
            spawn_result = self._spawn_worker(task_id, run_id_val)

            # 写入 STARTED 事件
            started_event = self._build_event(
                etype="WORKER_RUN_STARTED",
                task_id=task_id,
                run_id_str=run_id_val,
                payload={"mode": "async", "spawn_result": spawn_result},
                causation_id=intent_event.get("eventId"),
                idempotency_key=f"{self.project}:{task_id}:{run_id_val}:WORKER_RUN_STARTED",
            )
            self.sm.append_event(started_event)
            dispatched += 1

        return dispatched

    def _spawn_worker(self, task_id: str, run_id_val: str) -> dict:
        """Spawn 一个 Worker 子 agent 执行任务"""
        # 加载 Worker 模板
        template_path = self.base_dir.parent / "templates" / "worker-system.md"
        if template_path.exists():
            system_prompt = template_path.read_text()
        else:
            system_prompt = f"You are a Worker for project {self.project}."

        # 加载任务规格
        status = reduce_events(self.base_dir, emit_derived=False).status
        task_spec = {}
        for t in status.get("tasks", []):
            if t.get("taskId") == task_id:
                task_spec = t.get("taskSpec", {})
                break

        # 获取 Orchestrator session info
        orchestrator_session = self.session_label or f"tg:{self.project}:orchestrator"

        # 构建 Worker 任务提示
        task_prompt = f"""## Task: {task_id}

### Goal
{task_spec.get('goal', 'Complete the task')}

### Acceptance Criteria
"""
        for ac in task_spec.get('acceptance', []):
            task_prompt += f"- [ ] {ac}\n"

        task_prompt += f"""
### Context Files
"""
        for cf in task_spec.get('contextFiles', []):
            task_prompt += f"- {cf}\n"

        task_prompt += f"""
## Instructions
1. Read the task above carefully
2. Complete the work according to the acceptance criteria
3. When done, submit evidence to the Orchestrator:
   - Use `sessions_list` to find the Orchestrator session
   - Use `sessions_send --session-key {orchestrator_session} --message "<evidence>"` to submit

Project Root: {self.team.get('path', str(self.base_dir))}
"""

        full_prompt = f"{system_prompt}\n\n{task_prompt}"

        # 生成 Worker label
        worker_label = f"tg:{self.project}:worker:{task_id}"

        # 调用 sessions_spawn
        try:
            client = OpenClawClient()
            raw_result = client.sessions_spawn(
                task=full_prompt,
                label=worker_label,
                cleanup="keep"
            )
            # 解析返回值结构
            result_obj = raw_result.get("result", raw_result)
            details = result_obj.get("details", result_obj)
            
            # 有些情况下 details 里还有 content，需要解析
            if isinstance(details, dict) and "content" in details:
                content = details.get("content", [])
                if content and isinstance(content[0], dict) and "text" in content[0]:
                    import json
                    parsed = json.loads(content[0]["text"])
                    details = parsed
            
            return {
                "status": "spawned",
                "session_key": details.get("childSessionKey"),
                "run_id": run_id_val,
                "runId": details.get("runId"),
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "run_id": run_id_val,
            }

        return dispatched

    def _check_worker_timeouts(self, status: dict):
        """检测 Worker 超时（30分钟无进展）"""
        timeout = timedelta(minutes=self.config.worker_timeout_minutes)
        now = self._now()
        tasks = status.get("tasks", [])
        timed_out = []

        for task in tasks:
            task_id = task.get("taskId")
            state = task.get("state")
            run_id_val = task.get("runId")

            # 只检查 running 状态的任务
            if state != "running" or not run_id_val:
                continue

            # 获取任务开始时间（从 events 推导）
            events_path = self.base_dir / "audit" / "events.ndjson"
            if not events_path.exists():
                continue

            events, _ = read_events(events_path)
            start_time = None
            for ev in events:
                if ev.get("taskId") == task_id and ev.get("runId") == run_id_val:
                    if ev.get("type") == "WORKER_RUN_STARTED":
                        ts = self._parse_time(ev.get("at"))
                        if ts:
                            start_time = ts
                            break

            if not start_time:
                continue

            if (now - start_time) > timeout:
                timed_out.append((task_id, run_id_val))

        # 写入超时事件
        for task_id, run_id_val in timed_out:
            failed_event = self._build_event(
                etype="WORKER_RUN_FAILED",
                task_id=task_id,
                run_id_str=run_id_val,
                payload={"reason": "worker_timeout"},
                causation_id=None,
                idempotency_key=f"{self.project}:{task_id}:{run_id_val}:WORKER_RUN_FAILED:timeout",
            )
            self.sm.append_event(failed_event)

            close_event = self._build_event(
                etype="RUN_CLOSED",
                task_id=task_id,
                run_id_str=run_id_val,
                payload={"closeReason": "worker_timeout"},
                causation_id=failed_event.get("eventId"),
                idempotency_key=f"{self.project}:{task_id}:{run_id_val}:RUN_CLOSED:timeout",
            )
            self.sm.append_event(close_event)

    def _auto_retry_blocked(self, status: dict) -> int:
        """
        自动重试 blocked 任务（可选功能，默认关闭）
        需要配置 max_retries 才生效

        Returns:
            重试的任务数
        """
        if self.config.max_retries <= 0:
            return 0  # 自动重试关闭

        tasks = status.get("tasks", [])
        retried = 0

        for task in tasks:
            task_id = task.get("taskId")
            state = task.get("state")

            # 只处理 blocked 状态的任务
            if state != "blocked":
                continue

            # 检查重试次数
            retry_count = self._get_retry_count(task_id)
            if retry_count >= self.config.max_retries:
                continue  # 超过最大重试次数

            # 生成新 runId 并重试
            new_run = run_id_gen("r")
            retry_event = self._build_event(
                etype="WORKER_RUN_INTENT",
                task_id=task_id,
                run_id_str=new_run,
                payload={"reason": f"auto_retry_{retry_count + 1}"},
                causation_id=None,
                idempotency_key=f"{self.project}:{task_id}:{new_run}:WORKER_RUN_INTENT:retry",
            )
            self.sm.append_event(retry_event)

            # 记录重试
            self.sm.append_event({
                "type": "TASK_RETRIED",
                "actor": "orchestrator",
                "project": self.project,
                "taskId": task_id,
                "runId": new_run,
                "payload": {
                    "retryCount": retry_count + 1,
                    "previousRunId": task.get("runId"),
                    "reason": "auto_retry_after_failure",
                },
                "idempotencyKey": f"{self.project}:{task_id}:{new_run}:TASK_RETRIED:{retry_count + 1}",
            })

            retried += 1

        return retried

    def _get_retry_count(self, task_id: str) -> int:
        """获取任务已重试次数"""
        events_path = self.base_dir / "audit" / "events.ndjson"
        if not events_path.exists():
            return 0

        retry_count = 0
        with open(events_path, "r") as f:
            for line in f:
                if "TASK_RETRIED" in line and task_id in line:
                    retry_count += 1

        return retry_count

    def _reconcile_open_runs(self, events: List[dict]):
        # Build run state from events
        run_info: Dict[Tuple[str, str], Dict[str, Any]] = {}
        events_sorted = sorted(events, key=lambda e: (int(e.get("sequenceNumber", 0)), e.get("eventId", "")))

        def get_run(task_id: str, run_id: str) -> Dict[str, Any]:
            key = (task_id, run_id)
            if key not in run_info:
                run_info[key] = {
                    "taskId": task_id,
                    "runId": run_id_val,
                    "closed": False,
                    "completed": False,
                    "failed": False,
                    "aborted": False,
                    "verdict": None,
                    "intent_at": None,
                    "started_at": None,
                    "verdict_event_id": None,
                    "failed_event_id": None,
                    "aborted_event_id": None,
                    "completed_event_id": None,
                }
            return run_info[key]

        for ev in events_sorted:
            task_id = ev.get("taskId")
            run_id_val = ev.get("runId")
            if not task_id or not run_id_val:
                continue
            info = get_run(task_id, run_id_val)
            etype = ev.get("type")
            payload = ev.get("payload", {}) or {}
            ts = self._parse_time(ev.get("at"))
            if etype == "WORKER_RUN_INTENT":
                if ts and info.get("intent_at") is None:
                    info["intent_at"] = ts
            elif etype == "WORKER_RUN_STARTED":
                if ts and info.get("started_at") is None:
                    info["started_at"] = ts
            elif etype == "WORKER_RUN_COMPLETED":
                info["completed"] = True
                info["completed_event_id"] = ev.get("eventId")
            elif etype == "WORKER_RUN_FAILED":
                info["failed"] = True
                info["failed_event_id"] = ev.get("eventId")
            elif etype == "WORKER_RUN_ABORTED":
                info["aborted"] = True
                info["aborted_event_id"] = ev.get("eventId")
            elif etype in ("WATCHDOG_VERDICT", "HUMAN_VERDICT"):
                info["verdict"] = payload.get("verdict")
                info["verdict_event_id"] = ev.get("eventId")
            elif etype == "RUN_CLOSED":
                info["closed"] = True

        now = self._now()
        stale_sec = 30 * 60

        for info in run_info.values():
            if info.get("closed"):
                continue

            verdict = info.get("verdict")
            closed_condition = False
            if verdict == "BLOCK" or info.get("failed"):
                closed_condition = True
            elif info.get("aborted"):
                closed_condition = True
            elif info.get("completed") and verdict == "PASS":
                closed_condition = True

            task_id = info["taskId"]
            run_id_val = info["runId"]

            if closed_condition:
                causation_id = info.get("verdict_event_id") or info.get("failed_event_id") or info.get("aborted_event_id") or info.get("completed_event_id")
                close_event = self._build_event(
                    etype="RUN_CLOSED",
                    task_id=task_id,
                    run_id_str=run_id_val,
                    payload={"closeReason": "recovered_close", "verdictEventId": info.get("verdict_event_id")},
                    causation_id=causation_id,
                    idempotency_key=f"{self.project}:{task_id}:{run_id_val}:RUN_CLOSED",
                )
                self.sm.append_event(close_event)
                continue

            baseline = info.get("intent_at") or info.get("started_at")
            if not baseline:
                continue
            if (now - baseline).total_seconds() < stale_sec:
                continue

            failed_event = self._build_event(
                etype="WORKER_RUN_FAILED",
                task_id=task_id,
                run_id_str=run_id_val,
                payload={"reason": "stale after restart"},
                idempotency_key=f"{self.project}:{task_id}:{run_id_val}:WORKER_RUN_FAILED",
            )
            failed_res = self.sm.append_event(failed_event)
            failed_event_id = None
            if failed_res.get("event"):
                failed_event_id = failed_res["event"].get("eventId")
            close_event = self._build_event(
                etype="RUN_CLOSED",
                task_id=task_id,
                run_id_str=run_id_val,
                payload={"closeReason": "stale_after_restart", "verdictEventId": None},
                causation_id=failed_event_id,
                idempotency_key=f"{self.project}:{task_id}:{run_id_val}:RUN_CLOSED",
            )
            self.sm.append_event(close_event)

    def validate_incoming(self, *, actor: str, task_id: Optional[str], run_id: Optional[str], message_type: str) -> bool:
        if not task_id:
            return True

        status = reduce_events(self.base_dir, emit_derived=False).status
        expected = status.get("locks", {}).get("tasks", {}).get(task_id)

        if actor in ("worker", "watchdog"):
            if not run_id or run_id != expected:
                seed = f"{run_id}:{message_type}".encode("utf-8")
                digest = hashlib.sha256(seed).hexdigest()[:12]
                idempotency_key = f"{self.project}:{task_id}:{expected}:MESSAGE_IGNORED:{digest}"
                ignore_event = self._build_event(
                    etype="MESSAGE_IGNORED",
                    task_id=task_id,
                    run_id=expected,
                    payload={
                        "actor": actor,
                        "expectedRunId": expected,
                        "receivedRunId": run_id,
                        "messageType": message_type,
                    },
                    idempotency_key=idempotency_key,
                )
                self.sm.append_event(ignore_event)
                return False
            return True

        # PM/task-level messages do not require run_id binding
        return True

    def _handle_corrupted(self, corrupted: List[dict]):
        if not corrupted:
            return
        for c in corrupted:
            corrupted_event, recovery_event = self.sm.build_corrupted_event_payload(
                line_offset=c["line"],
                raw_line=c["raw"],
                reason=c["reason"],
                project=self.project,
            )
            self.sm.append_event(corrupted_event)
            self.sm.append_event(recovery_event)

    def _check_worker_evidence_files(self):
        """检测 Worker 写入的 evidence 文件并自动处理"""
        evidence_dir = self.base_dir / "evidence"
        if not evidence_dir.exists():
            return

        for task_dir in evidence_dir.iterdir():
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name

            # 查找未处理的 evidence 文件
            for evidence_file in task_dir.glob("*.md"):
                file_name = evidence_file.name
                # 跳过已经处理的文件（如 latest.md）
                if file_name == "latest.md":
                    continue

                # 解析 runId（文件名）
                if not file_name.endswith(".md"):
                    continue
                run_id_val = file_name[:-3]  # 去掉 .md 后缀

                # 检查是否已处理（检查 events 中是否已有对应的 EVIDENCE_SUBMITTED）
                events_path = self.base_dir / "audit" / "events.ndjson"
                already_processed = False
                if events_path.exists():
                    with open(events_path, "r") as f:
                        for line in f:
                            if run_id_val in line and "EVIDENCE_SUBMITTED" in line:
                                already_processed = True
                                break

                if already_processed:
                    continue

                # 读取 evidence 文件内容
                try:
                    content = evidence_file.read_text()
                except Exception:
                    continue

                # 提取 files changed
                files_changed = []
                for line in content.split("\n"):
                    if "- " in line and "Files Changed" not in line and "**" not in line:
                        f = line.strip().lstrip("- ")
                        if f:
                            files_changed.append(f)

                # 写入 EVIDENCE_SUBMITTED
                evidence_event = self._build_event(
                    etype="EVIDENCE_SUBMITTED",
                    task_id=task_id,
                    run_id_str=run_id_val,
                    payload={
                        "filesChanged": files_changed,
                        "evidencePath": str(evidence_file.relative_to(self.base_dir)),
                    },
                    idempotency_key=f"{self.project}:{task_id}:{run_id_val}:EVIDENCE_SUBMITTED",
                )
                self.sm.append_event(evidence_event)

                # 简化版 Watchdog：自动 PASS
                verdict_event = self._build_event(
                    etype="WATCHDOG_VERDICT",
                    task_id=task_id,
                    run_id_str=run_id_val,
                    payload={
                        "verdict": "PASS",
                        "reasons": [],
                        "suggestedActions": [],
                    },
                    idempotency_key=f"{self.project}:{task_id}:{run_id_val}:WATCHDOG_VERDICT:PASS",
                )
                self.sm.append_event(verdict_event)

                # 自动写入 COMPLETED
                completed_event = self._build_event(
                    etype="WORKER_RUN_COMPLETED",
                    task_id=task_id,
                    run_id_str=run_id_val,
                    payload={"result": "success"},
                    idempotency_key=f"{self.project}:{task_id}:{run_id_val}:WORKER_RUN_COMPLETED",
                )
                self.sm.append_event(completed_event)

                # 自动关闭 run
                close_event = self._build_event(
                    etype="RUN_CLOSED",
                    task_id=task_id,
                    run_id_str=run_id_val,
                    payload={"closeReason": "completed_with_pass"},
                    idempotency_key=f"{self.project}:{task_id}:{run_id_val}:RUN_CLOSED",
                )
                self.sm.append_event(close_event)

                print(f"[WORKER EVIDENCE] {task_id}/{run_id_val} auto-approved")

    def _process_worker_evidence(self, message: str, actor: str = "worker") -> bool:
        """
        处理 Worker 提交的 evidence

        解析 Worker 发送的消息格式：
        ## Evidence Submitted
        **Task**: <taskId>
        **Run**: <runId>
        **Files Changed**:
        - <file1>
        - <file2>

        Returns: True if evidence was processed
        """
        if "## Evidence Submitted" not in message:
            return False

        # 解析 taskId 和 runId
        task_id = None
        run_id_val = None
        files_changed = []

        for line in message.split("\n"):
            if "**Task**:" in line:
                task_id = line.split(":")[-1].strip()
            elif "**Run**:" in line:
                run_id_val = line.split(":")[-1].strip()
            elif "- " in line and "Files Changed" not in line:
                files_changed.append(line.strip().lstrip("- "))

        if not task_id or not run_id_val:
            return False

        # 查找任务对应的 run
        status = reduce_events(self.base_dir, emit_derived=False).status
        task_locks = status.get("locks", {}).get("tasks", {})
        expected_run = task_locks.get(task_id)

        # 验证 runId 匹配
        if expected_run and run_id != expected_run:
            # 忽略不匹配的 run
            return False

        # 写入 EVIDENCE_SUBMITTED
        evidence_event = self._build_event(
            etype="EVIDENCE_SUBMITTED",
            task_id=task_id,
            run_id_str=run_id_val,
            payload={
                "filesChanged": files_changed,
                "evidenceText": message[:500],  # 截取前 500 字符
            },
            idempotency_key=f"{self.project}:{task_id}:{run_id_val}:EVIDENCE_SUBMITTED",
        )
        self.sm.append_event(evidence_event)

        # 简化版 Watchdog：自动 PASS（后续可以加入真实校验）
        verdict_event = self._build_event(
            etype="WATCHDOG_VERDICT",
            task_id=task_id,
            run_id_str=run_id_val,
            payload={
                "verdict": "PASS",
                "reasons": [],
                "suggestedActions": [],
            },
            idempotency_key=f"{self.project}:{task_id}:{run_id_val}:WATCHDOG_VERDICT:PASS",
        )
        self.sm.append_event(verdict_event)

        # 自动写入 COMPLETED
        completed_event = self._build_event(
            etype="WORKER_RUN_COMPLETED",
            task_id=task_id,
            run_id_str=run_id_val,
            payload={"result": "success"},
            idempotency_key=f"{self.project}:{task_id}:{run_id_val}:WORKER_RUN_COMPLETED",
        )
        self.sm.append_event(completed_event)

        # 自动关闭 run
        close_event = self._build_event(
            etype="RUN_CLOSED",
            task_id=task_id,
            run_id_str=run_id_val,
            payload={"closeReason": "completed_with_pass"},
            idempotency_key=f"{self.project}:{task_id}:{run_id_val}:RUN_CLOSED",
        )
        self.sm.append_event(close_event)

        print(f"[WORKER EVIDENCE] {task_id} completed with PASS")
        return True

    def _process_worker_message(self, message: str, actor: str = "worker") -> dict:
        """
        处理来自 Worker 的消息

        Returns: 处理结果 dict
        """
        # 尝试解析 evidence 提交
        if self._process_worker_evidence(message, actor):
            return {"status": "evidence_processed"}

        # 其他消息类型可以扩展处理
        return {"status": "ignored", "reason": "no_handler"}

    def suggest_skills(self, status: Optional[dict] = None) -> List[str]:
        status = status or reduce_events(self.base_dir, emit_derived=False).status
        registry = SkillRegistry.load(self.base_dir / "registry.json")
        memory = self.team.get("defaults", {}).get("skillMemory", {})
        router = SkillRouter(registry, memory)
        prompts: List[str] = []
        for task in status.get("tasks", []):
            gates = task.get("gates") or []
            if "awaiting_skill_decision" not in gates:
                continue
            task_spec = task.get("taskSpec") or {"taskId": task.get("taskId")}
            suggestion = router.suggest(task_spec)
            prompts.append(router.build_prompt(self.project, suggestion))
        return prompts

    def _aggregate_results(self, status: dict) -> List[dict]:
        """聚合已完成的任务结果"""
        results = []
        tasks = status.get("tasks", [])

        for task in tasks:
            task_id = task.get("taskId")
            state = task.get("state")
            last_run_id = task.get("runId") or task.get("lastRunId")
            result = task.get("result", {})
            last_verdict = task.get("lastVerdict", {})
            last_evidence = task.get("lastEvidence", {})

            # 只处理 done 或 blocked 状态的任务
            # done 任务没有 state 字段（状态分片优化），blocked 任务有 state=blocked
            is_done = state is None and last_run_id is not None  # 没有 state 但有 runId 意味着 done
            is_blocked = state == "blocked"

            if not is_done and not is_blocked:
                continue

            # 跳过已通知过的任务（幂等）
            result_key = f"{task_id}:{last_run_id}:notified"
            # 使用 events.ndjson 检查是否已通知过
            events_path = self.base_dir / "audit" / "events.ndjson"
            if events_path.exists():
                with open(events_path, "r") as f:
                    for line in f:
                        if result_key in line:
                            continue  # 已通知

            # 确定结果状态
            final_state = "done" if is_done else "blocked"

            results.append({
                "taskId": task_id,
                "state": final_state,
                "runId": last_run_id,
                "result": result,
                "verdict": last_verdict.get("verdict"),
                "evidencePath": last_evidence.get("evidencePath"),
                "quality": result.get("quality"),
            })

        return results

    def _notify_result(self, result: dict):
        """发送结果通知（telegram/console）"""
        task_id = result["taskId"]
        state = result["state"]
        last_run_id = result["runId"]
        verdict = result.get("verdict")
        quality = result.get("quality", "clean")

        # 构建通知消息
        if state == "done":
            if quality == "warn_override":
                msg = f"⚠️ [{self.project}] {task_id} 完成（人工Override）"
            else:
                msg = f"✅ [{self.project}] {task_id} 完成"
        elif state == "blocked":
            failure = result.get("result", {}).get("failureReason", "unknown")
            msg = f"❌ [{self.project}] {task_id} 失败: {failure}"
        else:
            return  # 只通知 done/blocked

        # 标记已通知
        events_path = self.base_dir / "audit" / "events.ndjson"
        notification_event = {
            "type": "RESULT_NOTIFIED",
            "actor": "orchestrator",
            "project": self.project,
            "taskId": task_id,
            "runId": last_run_id,
            "payload": {
                "channel": "telegram",
                "message": msg,
            },
            "idempotencyKey": f"{self.project}:{task_id}:{last_run_id}:notified",
        }
        self.sm.append_event(notification_event)

        # TODO: 实际发送 telegram 通知（通过 message 工具）
        # 目前只打印到 console
        print(f"[NOTIFICATION] {msg}")

    def _process_results(self, status: dict):
        """处理并通知任务结果"""
        results = self._aggregate_results(status)
        for result in results:
            self._notify_result(result)

    def tick(self):
        events_path = self.base_dir / "audit" / "events.ndjson"
        events, corrupted = read_events(events_path)
        if corrupted:
            self._handle_corrupted(corrupted)
            events, _ = read_events(events_path)

        # Enforce BLOCK sequence + watchdog heartbeat
        self._enforce_block_sequence(events)
        self._watchdog_heartbeat(events)

        # Restart reconciliation (close/stale runs)
        self._reconcile_open_runs(events)

        # Recompute status after enforcement
        result = reduce_events(self.base_dir)

        # Auto-dispatch pending tasks (Phase 1: minimal dispatch)
        dispatched = self._dispatch_pending_tasks(result.status)

        # Check worker timeouts
        self._check_worker_timeouts(result.status)

        # Process and notify results (Phase 2: result consumption)
        self._process_results(result.status)

        # Auto-retry blocked tasks (Phase 3: retry strategy)
        retried = self._auto_retry_blocked(result.status)

        # Check for Worker evidence files (Phase 4: auto-approval)
        self._check_worker_evidence_files()

        # Recompute status after dispatch/timeout/retry
        result = reduce_events(self.base_dir)
        self.sm.write_status(result.status)
        return result

    def run_loop(self, interval_sec: float = 10.0):
        import time
        while True:
            self.tick()
            time.sleep(interval_sec)
