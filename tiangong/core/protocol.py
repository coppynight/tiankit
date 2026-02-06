from __future__ import annotations

import json
import zlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Literal, Union


def canonical_json(event: Dict[str, Any]) -> str:
    event_copy = {**event, "crc32": ""}
    return json.dumps(event_copy, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_json_bytes(event: Dict[str, Any]) -> bytes:
    return canonical_json(event).encode("utf-8")


def compute_crc32(event: Dict[str, Any]) -> str:
    crc = zlib.crc32(canonical_json_bytes(event)) & 0xFFFFFFFF
    return f"{crc:08X}"


def verify_crc32(event: Dict[str, Any]) -> bool:
    crc = event.get("crc32")
    if not crc:
        return False
    try:
        return crc == compute_crc32(event)
    except Exception:
        return False


TaskKind = Literal["coding", "build_test", "docs", "research", "ops", "design", "comms"]
RiskLevel = Literal["low", "medium", "high"]


@dataclass
class TaskSpec:
    taskId: str
    goal: str
    kind: TaskKind
    acceptance: List[str]
    dependencies: List[str] = field(default_factory=list)
    contextFiles: List[str] = field(default_factory=list)
    suggestedSkills: List[str] = field(default_factory=list)
    preferredSkill: Optional[str] = None
    fallbackSkills: List[str] = field(default_factory=list)
    riskLevel: Optional[RiskLevel] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceCommand:
    cmd: str
    output: str = ""


@dataclass
class PathSafety:
    pwd: str
    repoRoot: str
    changedFiles: List[str] = field(default_factory=list)


@dataclass
class EvidenceChain:
    taskId: str
    runId: str
    status: Literal["done", "blocked"]
    files: List[str] = field(default_factory=list)
    keyLines: List[str] = field(default_factory=list)
    commands: List[EvidenceCommand] = field(default_factory=list)
    diffPatchPath: Optional[str] = None
    validationScript: Optional[str] = None
    pathSafety: Optional[PathSafety] = None
    blockingIssues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["commands"] = [asdict(cmd) for cmd in self.commands]
        if self.pathSafety:
            data["pathSafety"] = asdict(self.pathSafety)
        return data


@dataclass
class Verdict:
    taskId: str
    runId: str
    verdict: Literal["PASS", "WARN", "BLOCK"]
    reasons: List[str] = field(default_factory=list)
    suggestedActions: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectProgress:
    total: int = 0
    done: int = 0
    blocked: int = 0


@dataclass
class ProjectStatus:
    name: str = "unknown"
    phase: Literal["running", "finished", "halted"] = "running"
    halted: bool = False
    mode: Literal["normal", "degraded"] = "normal"
    degradedReason: Optional[str] = None
    progress: ProjectProgress = field(default_factory=ProjectProgress)


@dataclass
class WatchdogStatus:
    lastHeartbeatAt: Optional[str] = None
    state: Literal["healthy", "unresponsive"] = "healthy"


@dataclass
class LocksStatus:
    project: Literal["idle", "running"] = "idle"
    tasks: Dict[str, str] = field(default_factory=dict)


@dataclass
class TaskStatus:
    taskId: str
    state: Optional[str] = None
    gates: List[str] = field(default_factory=list)
    runId: Optional[str] = None
    skillDecision: Dict[str, Any] = field(default_factory=dict)
    policyTier: Optional[str] = None
    lastEvidence: Dict[str, Any] = field(default_factory=dict)
    lastVerdict: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)
    resultSummary: Optional[str] = None
    evidencePath: Optional[str] = None
    lastRunId: Optional[str] = None


@dataclass
class Status:
    project: ProjectStatus = field(default_factory=ProjectStatus)
    watchdog: WatchdogStatus = field(default_factory=WatchdogStatus)
    tasks: List[TaskStatus] = field(default_factory=list)
    risks: List[Dict[str, Any]] = field(default_factory=list)
    alerts: List[Dict[str, Any]] = field(default_factory=list)
    locks: LocksStatus = field(default_factory=LocksStatus)
    updatedAt: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


EventType = Literal[
    "TEAM_CREATED",
    "PROJECT_STARTED",
    "PROJECT_FINISHED",
    "PROJECT_HALTED",
    "PROJECT_RESUMED",
    "TASKSPEC_PUBLISHED",
    "TASK_SKILL_SET",
    "POLICY_TIER_REQUESTED",
    "POLICY_TIER_APPROVED",
    "WORKER_RUN_INTENT",
    "WORKER_RUN_STARTED",
    "WORKER_RUN_COMPLETED",
    "WORKER_RUN_FAILED",
    "WORKER_RUN_ABORTED",
    "RUN_CLOSED",
    "EVIDENCE_SUBMITTED",
    "WATCHDOG_VERDICT",
    "WATCHDOG_HEARTBEAT",
    "HUMAN_VERDICT",
    "PROJECT_MODE_RESTORED",
    "RECOVERY_STARTED",
    "MESSAGE_IGNORED",
    "WATCHDOG_UNRESPONSIVE",
    "VERDICT_TIMEOUT",
    "LOCK_TIMEOUT_DETECTED",
    "CORRUPTED_LINE_DETECTED",
]


@dataclass
class EventBase:
    type: EventType
    eventId: Optional[str] = None
    sequenceNumber: Optional[int] = None
    schemaVersion: int = 1
    at: Optional[str] = None
    actor: Optional[str] = None
    sessionLabel: Optional[str] = None
    project: str = "unknown"
    taskId: Optional[str] = None
    runId: Optional[str] = None
    correlationId: Optional[str] = None
    causationId: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    idempotencyKey: Optional[str] = None
    crc32: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TeamCreated(EventBase):
    type: Literal["TEAM_CREATED"] = "TEAM_CREATED"


@dataclass
class ProjectStarted(EventBase):
    type: Literal["PROJECT_STARTED"] = "PROJECT_STARTED"


@dataclass
class ProjectFinished(EventBase):
    type: Literal["PROJECT_FINISHED"] = "PROJECT_FINISHED"


@dataclass
class ProjectHalted(EventBase):
    type: Literal["PROJECT_HALTED"] = "PROJECT_HALTED"


@dataclass
class ProjectResumed(EventBase):
    type: Literal["PROJECT_RESUMED"] = "PROJECT_RESUMED"


@dataclass
class TaskSpecPublished(EventBase):
    type: Literal["TASKSPEC_PUBLISHED"] = "TASKSPEC_PUBLISHED"


@dataclass
class TaskSkillSet(EventBase):
    type: Literal["TASK_SKILL_SET"] = "TASK_SKILL_SET"


@dataclass
class PolicyTierRequested(EventBase):
    type: Literal["POLICY_TIER_REQUESTED"] = "POLICY_TIER_REQUESTED"


@dataclass
class PolicyTierApproved(EventBase):
    type: Literal["POLICY_TIER_APPROVED"] = "POLICY_TIER_APPROVED"


@dataclass
class WorkerRunIntent(EventBase):
    type: Literal["WORKER_RUN_INTENT"] = "WORKER_RUN_INTENT"


@dataclass
class WorkerRunStarted(EventBase):
    type: Literal["WORKER_RUN_STARTED"] = "WORKER_RUN_STARTED"


@dataclass
class WorkerRunCompleted(EventBase):
    type: Literal["WORKER_RUN_COMPLETED"] = "WORKER_RUN_COMPLETED"


@dataclass
class WorkerRunFailed(EventBase):
    type: Literal["WORKER_RUN_FAILED"] = "WORKER_RUN_FAILED"


@dataclass
class WorkerRunAborted(EventBase):
    type: Literal["WORKER_RUN_ABORTED"] = "WORKER_RUN_ABORTED"


@dataclass
class RunClosed(EventBase):
    type: Literal["RUN_CLOSED"] = "RUN_CLOSED"


@dataclass
class EvidenceSubmitted(EventBase):
    type: Literal["EVIDENCE_SUBMITTED"] = "EVIDENCE_SUBMITTED"


@dataclass
class WatchdogVerdict(EventBase):
    type: Literal["WATCHDOG_VERDICT"] = "WATCHDOG_VERDICT"


@dataclass
class WatchdogHeartbeat(EventBase):
    type: Literal["WATCHDOG_HEARTBEAT"] = "WATCHDOG_HEARTBEAT"


@dataclass
class HumanVerdict(EventBase):
    type: Literal["HUMAN_VERDICT"] = "HUMAN_VERDICT"


@dataclass
class ProjectModeRestored(EventBase):
    type: Literal["PROJECT_MODE_RESTORED"] = "PROJECT_MODE_RESTORED"


@dataclass
class RecoveryStarted(EventBase):
    type: Literal["RECOVERY_STARTED"] = "RECOVERY_STARTED"


@dataclass
class MessageIgnored(EventBase):
    type: Literal["MESSAGE_IGNORED"] = "MESSAGE_IGNORED"


@dataclass
class WatchdogUnresponsive(EventBase):
    type: Literal["WATCHDOG_UNRESPONSIVE"] = "WATCHDOG_UNRESPONSIVE"


@dataclass
class VerdictTimeout(EventBase):
    type: Literal["VERDICT_TIMEOUT"] = "VERDICT_TIMEOUT"


@dataclass
class LockTimeoutDetected(EventBase):
    type: Literal["LOCK_TIMEOUT_DETECTED"] = "LOCK_TIMEOUT_DETECTED"


@dataclass
class CorruptedLineDetected(EventBase):
    type: Literal["CORRUPTED_LINE_DETECTED"] = "CORRUPTED_LINE_DETECTED"


Event = Union[
    TeamCreated,
    ProjectStarted,
    ProjectFinished,
    ProjectHalted,
    ProjectResumed,
    TaskSpecPublished,
    TaskSkillSet,
    PolicyTierRequested,
    PolicyTierApproved,
    WorkerRunIntent,
    WorkerRunStarted,
    WorkerRunCompleted,
    WorkerRunFailed,
    WorkerRunAborted,
    RunClosed,
    EvidenceSubmitted,
    WatchdogVerdict,
    WatchdogHeartbeat,
    HumanVerdict,
    ProjectModeRestored,
    RecoveryStarted,
    MessageIgnored,
    WatchdogUnresponsive,
    VerdictTimeout,
    LockTimeoutDetected,
    CorruptedLineDetected,
]
