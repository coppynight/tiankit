import json
import os
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .protocol import verify_crc32
from .state_manager import atomic_write_json

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


@dataclass
class RunStatus:
    started: bool = False
    completed: bool = False
    verdict: Optional[str] = None
    aborted: bool = False
    failed: bool = False


@dataclass
class TaskState:
    task_id: str
    state: str = "pending"
    gates: set = field(default_factory=set)
    run_id: Optional[str] = None
    run_status: RunStatus = field(default_factory=RunStatus)
    skill_decision: dict = field(default_factory=dict)
    policy_tier: Optional[str] = None
    last_evidence: dict = field(default_factory=dict)
    last_verdict: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    task_spec: dict = field(default_factory=dict)


@dataclass
class ReplayResult:
    status: dict
    corrupted_lines: list
    alerts: list


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime(ISO_FORMAT)


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def read_events(events_path: Path) -> Tuple[List[dict], List[dict]]:
    events = []
    corrupted = []
    if not events_path.exists():
        return events, corrupted

    with open(events_path, "r", encoding="utf-8", errors="replace") as f:
        for idx, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except Exception as e:
                corrupted.append({
                    "line": idx,
                    "reason": f"json_decode_error: {e}",
                    "raw": raw,
                })
                continue
            # crc32 check
            if not verify_crc32(event):
                corrupted.append({
                    "line": idx,
                    "reason": "crc_mismatch",
                    "raw": raw,
                })
                continue
            events.append(event)
    return events, corrupted


def apply_gate(task: TaskState, gate: str, add: bool):
    if add:
        task.gates.add(gate)
    else:
        task.gates.discard(gate)


def recompute_state(task: TaskState):
    verdict = task.run_status.verdict
    # Priority: blocked > canceled > done
    if verdict == "BLOCK" or task.run_status.failed:
        task.state = "blocked"
        task.gates.clear()
        return
    if task.run_status.aborted:
        task.state = "canceled"
        task.gates.clear()
        return
    if task.run_status.completed and verdict == "PASS":
        task.state = "done"
        task.result = {"quality": "clean", **task.result}
        task.gates.clear()
        return


def build_base_status(project_name: str) -> dict:
    return {
        "project": {
            "name": project_name,
            "phase": "running",
            "halted": False,
            "mode": "normal",
            "degradedReason": None,
            "progress": {"total": 0, "done": 0, "blocked": 0},
        },
        "watchdog": {
            "lastHeartbeatAt": None,
            "state": "healthy",
        },
        "tasks": [],
        "risks": [],
        "alerts": [],
        "locks": {
            "project": "idle",
            "tasks": {},
        },
        "updatedAt": utc_now(),
    }


def reduce_events(base_dir: Path, emit_derived: bool = True) -> ReplayResult:
    base_dir = Path(base_dir)
    audit_dir = base_dir / "audit"
    derived_dir = base_dir / "derived"
    events_path = audit_dir / "events.ndjson"

    events, corrupted = read_events(events_path)
    # sort by sequenceNumber (fallback to 0)
    events.sort(key=lambda e: (int(e.get("sequenceNumber", 0)), e.get("eventId", "")))

    project_name = None
    status = build_base_status("unknown")
    tasks: Dict[str, TaskState] = {}
    open_runs: Dict[str, List[str]] = {}
    locks_project_running = False

    def get_task(tid: str) -> TaskState:
        if tid not in tasks:
            tasks[tid] = TaskState(task_id=tid)
        return tasks[tid]

    seen_keys = set()

    for event in events:
        key = event.get("idempotencyKey")
        if key:
            if key in seen_keys:
                continue
            seen_keys.add(key)

        etype = event.get("type")
        project_name = event.get("project") or project_name or "unknown"
        status["project"]["name"] = project_name

        task_id = event.get("taskId")
        run_id = event.get("runId")
        payload = event.get("payload", {}) or {}

        if etype == "TEAM_CREATED":
            status["project"]["name"] = project_name
        elif etype == "PROJECT_STARTED":
            locks_project_running = True
            status["project"]["phase"] = "running"
        elif etype == "PROJECT_FINISHED":
            locks_project_running = False
            status["project"]["phase"] = "finished"
            status["project"]["halted"] = False
        elif etype == "PROJECT_HALTED":
            status["project"]["halted"] = True
            status["project"]["phase"] = "halted"
            locks_project_running = False
        elif etype == "PROJECT_RESUMED":
            status["project"]["halted"] = False
            status["project"]["phase"] = "running"
            locks_project_running = True
        elif etype == "PROJECT_MODE_RESTORED":
            status["project"]["mode"] = "normal"
            status["project"]["degradedReason"] = None
        elif etype in ("WATCHDOG_UNRESPONSIVE", "VERDICT_TIMEOUT", "RECOVERY_STARTED"):
            status["project"]["mode"] = "degraded"
            reason = {
                "WATCHDOG_UNRESPONSIVE": "watchdog_unresponsive",
                "VERDICT_TIMEOUT": "verdict_timeout",
                "RECOVERY_STARTED": "recovery_in_progress",
            }.get(etype)
            status["project"]["degradedReason"] = reason

        if etype == "WATCHDOG_HEARTBEAT":
            status["watchdog"]["lastHeartbeatAt"] = event.get("at")
            status["watchdog"]["state"] = "healthy"
        elif etype == "WATCHDOG_UNRESPONSIVE":
            status["watchdog"]["state"] = "unresponsive"

        # Telemetry events
        if etype in ("MESSAGE_IGNORED", "WATCHDOG_UNRESPONSIVE", "VERDICT_TIMEOUT", "LOCK_TIMEOUT_DETECTED", "CORRUPTED_LINE_DETECTED"):
            status["risks"].append({"type": etype, "eventId": event.get("eventId"), "payload": payload})

        if not task_id:
            continue

        task = get_task(task_id)
        run_bound_types = {
            "WORKER_RUN_STARTED",
            "WORKER_RUN_COMPLETED",
            "WORKER_RUN_FAILED",
            "WORKER_RUN_ABORTED",
            "EVIDENCE_SUBMITTED",
            "WATCHDOG_VERDICT",
            "HUMAN_VERDICT",
        }
        if etype in run_bound_types and task.run_id and run_id and run_id != task.run_id:
            continue
        if etype == "TASKSPEC_PUBLISHED":
            specs = payload.get("tasks") or []
            if specs:
                for spec in specs:
                    tid = spec.get("taskId") or task_id
                    t = get_task(tid)
                    t.state = "pending"
                    apply_gate(t, "awaiting_skill_decision", True)
                    t.task_spec = spec
            else:
                task.state = "pending"
                apply_gate(task, "awaiting_skill_decision", True)
                task.task_spec = payload
        elif etype == "TASK_SKILL_SET":
            apply_gate(task, "awaiting_skill_decision", False)
            task.skill_decision = {"chosenSkill": payload.get("chosenSkill"), "decisionSeq": payload.get("decisionSeq")}
        elif etype == "POLICY_TIER_REQUESTED":
            apply_gate(task, "awaiting_policy_approval", True)
        elif etype == "POLICY_TIER_APPROVED":
            apply_gate(task, "awaiting_policy_approval", False)
            task.policy_tier = payload.get("tier")
        elif etype == "VERDICT_TIMEOUT":
            apply_gate(task, "needs_human_review", True)
        elif etype == "WORKER_RUN_INTENT":
            if task.run_id != run_id:
                task.run_status = RunStatus()
                task.last_evidence = {}
                task.last_verdict = {}
                task.result = {}
            task.state = "assigned"
            task.run_id = run_id
            open_runs.setdefault(task_id, []).append(run_id)
        elif etype == "WORKER_RUN_STARTED":
            task.state = "running"
            task.run_id = run_id
            task.run_status.started = True
        elif etype == "WORKER_RUN_COMPLETED":
            task.run_status.completed = True
            task.run_id = run_id
            recompute_state(task)
        elif etype == "WORKER_RUN_FAILED":
            task.run_status.failed = True
            task.run_id = run_id
            reason = payload.get("reason") or payload.get("error") or payload.get("message")
            if reason:
                task.result = {"failureReason": reason, **task.result}
            recompute_state(task)
        elif etype == "WORKER_RUN_ABORTED":
            task.run_status.aborted = True
            task.run_id = run_id
            recompute_state(task)
        elif etype == "EVIDENCE_SUBMITTED":
            apply_gate(task, "awaiting_verdict", True)
            task.last_evidence = payload
        elif etype == "WATCHDOG_VERDICT":
            verdict = payload.get("verdict")
            task.run_status.verdict = verdict
            task.last_verdict = payload
            apply_gate(task, "awaiting_verdict", False)
            if verdict == "WARN":
                apply_gate(task, "needs_human_review", True)
            elif verdict == "BLOCK":
                task.state = "blocked"
                task.gates.clear()
                status["alerts"].append({"type": "blocked", "taskId": task_id, "runId": run_id})
            recompute_state(task)
        elif etype == "HUMAN_VERDICT":
            verdict = payload.get("verdict")
            task.run_status.verdict = verdict
            task.last_verdict = payload
            if verdict == "PASS":
                apply_gate(task, "needs_human_review", False)
                task.result = {"quality": "warn_override", **task.result}
            elif verdict == "BLOCK":
                task.state = "blocked"
                task.gates.clear()
            recompute_state(task)
        elif etype == "RUN_CLOSED":
            # release lock for this run
            if task_id in open_runs and run_id in open_runs[task_id]:
                open_runs[task_id] = [r for r in open_runs[task_id] if r != run_id]
                if not open_runs[task_id]:
                    open_runs.pop(task_id, None)

    if status["project"]["degradedReason"] == "watchdog_unresponsive":
        for task in tasks.values():
            if task.state in ("done", "blocked", "canceled"):
                continue
            if "awaiting_verdict" in task.gates:
                apply_gate(task, "needs_human_review", True)

    # derive locks
    status["locks"]["project"] = "running" if locks_project_running and not status["project"]["halted"] else "idle"

    locks_tasks = {}
    for task_id, run_ids in open_runs.items():
        if len(run_ids) == 1:
            locks_tasks[task_id] = run_ids[0]
        else:
            status["project"]["mode"] = "degraded"
            status["project"]["degradedReason"] = "multiple_open_runs"
            status["alerts"].append({"type": "multiple_open_runs", "taskId": task_id, "runIds": run_ids})
    status["locks"]["tasks"] = locks_tasks

    # finalize tasks list + progress
    tasks_out = []
    done_count = 0
    blocked_count = 0
    for task in tasks.values():
        if task.state == "done":
            done_count += 1
            tasks_out.append({
                "taskId": task.task_id,
                "resultSummary": task.result.get("summary"),
                "evidencePath": task.last_evidence.get("evidencePath"),
                "lastRunId": task.run_id,
                "taskSpec": task.task_spec or None,
            })
        else:
            if task.state == "blocked":
                blocked_count += 1
            tasks_out.append({
                "taskId": task.task_id,
                "state": task.state,
                "gates": sorted(list(task.gates)),
                "runId": task.run_id,
                "skillDecision": task.skill_decision,
                "policyTier": task.policy_tier,
                "lastEvidence": task.last_evidence,
                "lastVerdict": task.last_verdict,
                "result": task.result,
                "taskSpec": task.task_spec or None,
            })

    status["tasks"] = tasks_out
    status["project"]["progress"] = {
        "total": len(tasks_out),
        "done": done_count,
        "blocked": blocked_count,
    }
    status["updatedAt"] = utc_now()

    # Write derived outputs
    alerts = []
    if corrupted:
        for c in corrupted:
            alerts.append({
                "type": "corrupted_line",
                "line": c["line"],
                "reason": c["reason"],
                "hash": sha256_hex(c["raw"]),
            })
        status["alerts"].extend(alerts)

    if emit_derived:
        derived_dir.mkdir(parents=True, exist_ok=True)
        # derived verdicts
        verdicts_path = derived_dir / "watchdog-verdicts.ndjson"
        with open(verdicts_path, "w", encoding="utf-8") as f:
            for ev in events:
                if ev.get("type") == "WATCHDOG_VERDICT":
                    f.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n")
        # locks index
        locks_index_path = derived_dir / "locks-index.json"
        atomic_write_json(locks_index_path, status["locks"])

    return ReplayResult(status=status, corrupted_lines=corrupted, alerts=alerts)
