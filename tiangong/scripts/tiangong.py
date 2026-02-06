#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPT_DIR = Path(__file__).resolve().parent
TOOL_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(TOOL_ROOT))

from core.state_manager import StateManager
from core.reducer import reduce_events, read_events
from core.orchestrator import Orchestrator, OrchestratorConfig
from core.ids import run_id
from core.openclaw_client import OpenClawClient
from core.skill_registry import SkillRegistry
from core.skill_router import SkillRouter


def projects_root() -> Path:
    return TOOL_ROOT / "projects"


def project_dir(name: str) -> Path:
    return projects_root() / name


def tiangong_dir(name: str) -> Path:
    return project_dir(name) / ".tiangong"


def load_events_sorted(base_dir: Path):
    events, corrupted = read_events(base_dir / "audit" / "events.ndjson")
    events.sort(key=lambda e: (int(e.get("sequenceNumber", 0)), e.get("eventId", "")))
    return events, corrupted


def load_status(base_dir: Path):
    return reduce_events(base_dir).status


def cmd_list(_args):
    root = projects_root()
    if not root.exists():
        print("No projects directory.")
        return 1
    for p in sorted(root.iterdir()):
        if p.is_dir():
            print(p.name)
    return 0


def cmd_create(args):
    script = TOOL_ROOT / "scripts" / "start-project.sh"
    if not script.exists():
        print("ERROR: start-project.sh not found.")
        return 1
    plan = args.plan or ""
    rc = __import__("subprocess").run([str(script), args.project, args.root, plan]).returncode
    if rc != 0:
        return rc
    if args.no_spawn:
        return 0

    spawn_args = SimpleNamespace(project=args.project, session_key=args.session_key)
    return cmd_oc_start(spawn_args)


def cmd_status(args):
    base_dir = tiangong_dir(args.project)
    status = load_status(base_dir)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


def cmd_progress(args):
    base_dir = tiangong_dir(args.project)
    status = load_status(base_dir)
    proj = status.get("project", {})
    progress = proj.get("progress", {})
    print(f"{args.project}: {progress.get('done', 0)}/{progress.get('total', 0)} done, {progress.get('blocked', 0)} blocked")
    return 0


def cmd_risks(args):
    base_dir = tiangong_dir(args.project)
    status = load_status(base_dir)
    proj = status.get("project", {})
    payload = {
        "project": {
            "mode": proj.get("mode"),
            "degradedReason": proj.get("degradedReason"),
            "halted": proj.get("halted"),
        },
        "risks": status.get("risks", []),
        "alerts": status.get("alerts", []),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_suggest_skill(args):
    base_dir = tiangong_dir(args.project)
    status = load_status(base_dir)
    task = _get_task(status, args.task_id)
    task_spec = (task or {}).get("taskSpec") or {}

    if args.kind:
        task_spec.setdefault("kind", args.kind)
    if args.suggested:
        merged = list(task_spec.get("suggestedSkills") or []) + list(args.suggested or [])
        task_spec["suggestedSkills"] = merged
    task_spec.setdefault("taskId", args.task_id)

    registry = SkillRegistry.load(base_dir / "registry.json")
    team_path = base_dir / "team.json"
    memory = {}
    if team_path.exists():
        try:
            data = json.loads(team_path.read_text(encoding="utf-8"))
            memory = data.get("defaults", {}).get("skillMemory", {})
        except Exception:
            memory = {}

    router = SkillRouter(registry, memory)
    suggestion = router.suggest(task_spec)

    if args.json:
        print(json.dumps(suggestion.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(router.build_prompt(args.project, suggestion))
    return 0


def cmd_start(args):
    base_dir = tiangong_dir(args.project)
    status = load_status(base_dir)
    proj = status.get("project", {})
    if proj.get("halted"):
        print("ERROR: project halted. Use 'tiangong resume <project>'.")
        return 1
    if status.get("locks", {}).get("project") == "running":
        print("Already running.")
        return 0
    start_id = run_id("start")
    event = {
        "type": "PROJECT_STARTED",
        "actor": "orchestrator",
        "project": args.project,
        "runId": start_id,
        "payload": {},
        "idempotencyKey": f"{args.project}:PROJECT_STARTED:{start_id}",
    }
    sm = StateManager(base_dir)
    sm.append_event(event)
    orch = Orchestrator(OrchestratorConfig(base_dir=base_dir))
    result = orch.tick()
    prompts = orch.suggest_skills(result.status)
    for prompt in prompts:
        print(prompt)
    print(f"Started project {args.project} (runId={start_id})")
    return 0


def cmd_resume(args):
    base_dir = tiangong_dir(args.project)
    status = load_status(base_dir)
    proj = status.get("project", {})
    if not proj.get("halted"):
        print("Project not halted.")
        return 0
    events, _ = load_events_sorted(base_dir)
    last_halt = None
    for ev in reversed(events):
        if ev.get("type") == "PROJECT_HALTED":
            last_halt = ev
            break
    halt_event_id = last_halt.get("eventId") if last_halt else "unknown"
    event = {
        "type": "PROJECT_RESUMED",
        "actor": "human",
        "project": args.project,
        "payload": {
            "resumeFrom": halt_event_id,
            "reason": args.reason or "manual_resume",
            "confirmedBy": args.confirmed_by or "human",
        },
        "idempotencyKey": f"{args.project}:PROJECT_RESUMED:{halt_event_id}",
    }
    sm = StateManager(base_dir)
    sm.append_event(event)
    Orchestrator(OrchestratorConfig(base_dir=base_dir)).tick()
    print(f"Resumed project {args.project}")
    return 0


def _get_task(status: dict, task_id: str) -> dict:
    for t in status.get("tasks", []):
        if t.get("taskId") == task_id:
            return t
    return {}


def cmd_choose_skill(args):
    base_dir = tiangong_dir(args.project)
    status = load_status(base_dir)
    events, _ = load_events_sorted(base_dir)
    decision_seq = 1
    for ev in events:
        if ev.get("type") == "TASK_SKILL_SET" and ev.get("taskId") == args.task_id:
            decision_seq = max(decision_seq, int(ev.get("payload", {}).get("decisionSeq", 0)) + 1)
    event = {
        "type": "TASK_SKILL_SET",
        "actor": "human",
        "project": args.project,
        "taskId": args.task_id,
        "payload": {"chosenSkill": args.skill, "decisionSeq": decision_seq},
        "idempotencyKey": f"{args.project}:{args.task_id}:TASK_SKILL_SET:{decision_seq}",
    }
    sm = StateManager(base_dir)
    sm.append_event(event)

    registry = SkillRegistry.load(base_dir / "registry.json")
    spec = registry.get(args.skill)
    tier = None
    if spec and spec.riskPolicy:
        tier = spec.riskPolicy.tier
    if tier and tier != "safe":
        req_event = {
            "type": "POLICY_TIER_REQUESTED",
            "actor": "orchestrator",
            "project": args.project,
            "taskId": args.task_id,
            "payload": {
                "requestedTier": tier,
                "reason": "skill_risk_policy",
                "skillName": args.skill,
            },
            "idempotencyKey": f"{args.project}:{args.task_id}:POLICY_TIER_REQUESTED:{tier}",
        }
        sm.append_event(req_event)

    orch = Orchestrator(OrchestratorConfig(base_dir=base_dir))
    orch.tick()

    status = load_status(base_dir)
    task = _get_task(status, args.task_id)
    task_spec = (task or {}).get("taskSpec") or {}
    kind = task_spec.get("kind")
    if kind:
        SkillRouter.update_skill_memory(base_dir / "team.json", kind, args.skill)

    print(f"Skill set for {args.task_id}: {args.skill} (seq={decision_seq})")
    return 0


def cmd_approve_tier(args):
    base_dir = tiangong_dir(args.project)
    status = load_status(base_dir)
    task = _get_task(status, args.task_id)
    run_id_val = task.get("runId")
    event = {
        "type": "POLICY_TIER_APPROVED",
        "actor": "human",
        "project": args.project,
        "taskId": args.task_id,
        "payload": {"tier": args.tier, "reason": args.reason or "approved"},
        "idempotencyKey": f"{args.project}:{args.task_id}:POLICY_TIER_APPROVED:{args.tier}",
    }
    if run_id_val:
        event["runId"] = run_id_val
        event["idempotencyKey"] = f"{args.project}:{args.task_id}:{run_id_val}:POLICY_TIER_APPROVED:{args.tier}"
    sm = StateManager(base_dir)
    sm.append_event(event)
    Orchestrator(OrchestratorConfig(base_dir=base_dir)).tick()
    print(f"Policy tier approved for {args.task_id}: {args.tier}")
    return 0


def cmd_human_verdict(args):
    base_dir = tiangong_dir(args.project)
    status = load_status(base_dir)
    task = _get_task(status, args.task_id)
    run_id_val = task.get("runId")
    if not run_id_val:
        print("ERROR: task has no active runId.")
        return 1
    verdict = args.verdict.upper()
    event = {
        "type": "HUMAN_VERDICT",
        "actor": "human",
        "project": args.project,
        "taskId": args.task_id,
        "runId": run_id_val,
        "payload": {
            "verdict": verdict,
            "reason": args.reason or "manual_verdict",
            "who": args.who or "human",
            "at": "now",
        },
        "idempotencyKey": f"{args.project}:{args.task_id}:{run_id_val}:HUMAN_VERDICT:{verdict}",
    }
    sm = StateManager(base_dir)
    sm.append_event(event)
    Orchestrator(OrchestratorConfig(base_dir=base_dir)).tick()
    print(f"Human verdict for {args.task_id}: {verdict}")
    return 0


def cmd_tick(args):
    base_dir = tiangong_dir(args.project)
    orch = Orchestrator(OrchestratorConfig(base_dir=base_dir))
    result = orch.tick()
    prompts = orch.suggest_skills(result.status)
    for prompt in prompts:
        print(prompt)
    print(f"OK: ticked {args.project}")
    return 0


def cmd_autopilot(args):
    """自动运行：定期 tick 直到项目完成"""
    import time
    base_dir = tiangong_dir(args.project)
    interval = args.interval  # 秒
    max_runs = args.max_runs

    print(f"Starting autopilot for {args.project} (interval={interval}s, max={max_runs})")

    for i in range(max_runs):
        try:
            orch = Orchestrator(OrchestratorConfig(base_dir=base_dir))
            result = orch.tick()
            prompts = orch.suggest_skills(result.status)

            # 检查进度
            status = result.status
            total = status.get("project", {}).get("progress", {}).get("total", 0)
            done = status.get("project", {}).get("progress", {}).get("done", 0)

            print(f"[{i+1}] {args.project}: {done}/{total} tasks done")

            if prompts:
                for prompt in prompts:
                    print(prompt)

            # 如果所有任务都完成，退出
            if total > 0 and done >= total:
                print(f"Project {args.project} completed!")
                return 0

            # 检查是否有正在运行的任务
            has_running = any(t.get("state") == "running" for t in status.get("tasks", []))
            if not has_running and prompts:
                print("Waiting for worker to spawn...")
                time.sleep(2)

        except Exception as e:
            print(f"Error: {e}")

        time.sleep(interval)

    print(f"Autopilot stopped after {max_runs} runs")
    return 0


def cmd_oc_check(args):
    client = OpenClawClient(session_key=args.session_key)
    resp = client.sessions_list(limit=5)
    print(json.dumps(resp, ensure_ascii=False, indent=2))
    return 0


def _read_text(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def cmd_oc_start(args):
    base_dir = tiangong_dir(args.project)
    team_path = base_dir / "team.json"
    if not team_path.exists():
        print("ERROR: team.json not found. Run create first.")
        return 1
    team = json.loads(team_path.read_text(encoding="utf-8"))
    project_root = team.get("path")
    plan_path = team.get("planPath")
    labels = team.get("labels", {})

    pm_label = labels.get("pm", f"tg:{args.project}:pm")
    watchdog_label = labels.get("watchdog", f"tg:{args.project}:watchdog")

    pm_system = _read_text(project_dir(args.project) / "pm" / "SYSTEM.md")
    watchdog_system = _read_text(project_dir(args.project) / "watchdog" / "SYSTEM.md")

    pm_task = (
        f"You are PM for project {args.project}.\n"
        f"Project root: {project_root}\n"
        f"Plan path: {plan_path}\n\n"
        f"Follow the SYSTEM rules below and output TaskSpec JSON.\n\n"
        f"{pm_system}"
    )

    watchdog_task = (
        f"You are Watchdog for project {args.project}.\n"
        f"Project root: {project_root}\n\n"
        f"Follow the SYSTEM rules below.\n\n"
        f"{watchdog_system}"
    )

    client = OpenClawClient(session_key=args.session_key)
    pm_resp = client.sessions_spawn(task=pm_task, label=pm_label, cleanup="keep")
    wd_resp = client.sessions_spawn(task=watchdog_task, label=watchdog_label, cleanup="keep")

    print("PM spawn:", json.dumps(pm_resp, ensure_ascii=False))
    print("Watchdog spawn:", json.dumps(wd_resp, ensure_ascii=False))
    print("Note: sub-agent outputs will announce to the target session.")
    return 0


def cmd_retry(args):
    base_dir = tiangong_dir(args.project)
    status = load_status(base_dir)
    proj = status.get("project", {})
    if proj.get("halted"):
        print("ERROR: project halted. Resume first.")
        return 1
    task = _get_task(status, args.task_id)
    new_run = run_id("r")
    event = {
        "type": "WORKER_RUN_INTENT",
        "actor": "orchestrator",
        "project": args.project,
        "taskId": args.task_id,
        "runId": new_run,
        "payload": {"reason": "manual_retry"},
        "idempotencyKey": f"{args.project}:{args.task_id}:{new_run}:WORKER_RUN_INTENT",
    }
    sm = StateManager(base_dir)
    sm.append_event(event)
    Orchestrator(OrchestratorConfig(base_dir=base_dir)).tick()
    print(f"Retry intent for {args.task_id}: runId={new_run}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="tiangong", description="Tiangong CLI (local)")
    sub = parser.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list")
    p_list.set_defaults(func=cmd_list)

    p_create = sub.add_parser("create")
    p_create.add_argument("project")
    p_create.add_argument("root")
    p_create.add_argument("plan", nargs="?")
    p_create.add_argument("--session-key", default="main")
    p_create.add_argument("--no-spawn", action="store_true")
    p_create.set_defaults(func=cmd_create)

    p_start = sub.add_parser("start")
    p_start.add_argument("project")
    p_start.set_defaults(func=cmd_start)

    p_status = sub.add_parser("status")
    p_status.add_argument("project")
    p_status.set_defaults(func=cmd_status)

    p_progress = sub.add_parser("progress")
    p_progress.add_argument("project")
    p_progress.set_defaults(func=cmd_progress)

    p_risks = sub.add_parser("risks")
    p_risks.add_argument("project")
    p_risks.set_defaults(func=cmd_risks)

    p_resume = sub.add_parser("resume")
    p_resume.add_argument("project")
    p_resume.add_argument("--reason")
    p_resume.add_argument("--confirmed-by")
    p_resume.set_defaults(func=cmd_resume)

    p_restore = sub.add_parser("restore")
    p_restore.add_argument("project")
    p_restore.add_argument("--reason")
    p_restore.add_argument("--confirmed-by")
    p_restore.set_defaults(func=cmd_resume)

    p_skill = sub.add_parser("choose-skill")
    p_skill.add_argument("project")
    p_skill.add_argument("task_id")
    p_skill.add_argument("skill")
    p_skill.set_defaults(func=cmd_choose_skill)

    p_select = sub.add_parser("select-skill")
    p_select.add_argument("project")
    p_select.add_argument("task_id")
    p_select.add_argument("skill")
    p_select.set_defaults(func=cmd_choose_skill)

    p_suggest = sub.add_parser("suggest-skill")
    p_suggest.add_argument("project")
    p_suggest.add_argument("task_id")
    p_suggest.add_argument("--kind")
    p_suggest.add_argument("--suggested", action="append")
    p_suggest.add_argument("--json", action="store_true")
    p_suggest.set_defaults(func=cmd_suggest_skill)

    p_tier = sub.add_parser("approve-tier")
    p_tier.add_argument("project")
    p_tier.add_argument("task_id")
    p_tier.add_argument("tier")
    p_tier.add_argument("--reason")
    p_tier.set_defaults(func=cmd_approve_tier)

    p_verdict = sub.add_parser("human-verdict")
    p_verdict.add_argument("project")
    p_verdict.add_argument("task_id")
    p_verdict.add_argument("verdict")
    p_verdict.add_argument("--reason")
    p_verdict.add_argument("--who")
    p_verdict.set_defaults(func=cmd_human_verdict)

    p_tick = sub.add_parser("tick")
    p_tick.add_argument("project")
    p_tick.set_defaults(func=cmd_tick)

    p_autopilot = sub.add_parser("autopilot")
    p_autopilot.add_argument("project")
    p_autopilot.add_argument("--interval", type=int, default=10, help="Tick interval in seconds")
    p_autopilot.add_argument("--max-runs", type=int, default=100, help="Max number of ticks")
    p_autopilot.set_defaults(func=cmd_autopilot)

    p_oc_check = sub.add_parser("oc-check")
    p_oc_check.add_argument("--session-key", default="main")
    p_oc_check.set_defaults(func=cmd_oc_check)

    p_oc_start = sub.add_parser("oc-start")
    p_oc_start.add_argument("project")
    p_oc_start.add_argument("--session-key", default="main")
    p_oc_start.set_defaults(func=cmd_oc_start)

    p_retry = sub.add_parser("retry")
    p_retry.add_argument("project")
    p_retry.add_argument("task_id")
    p_retry.set_defaults(func=cmd_retry)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
