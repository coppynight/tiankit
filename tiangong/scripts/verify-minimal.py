#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
TOOL_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(TOOL_ROOT))

from core.state_manager import StateManager
from core.reducer import reduce_events
from core.ids import run_id


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def ensure_git_repo(repo: Path):
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.name", "tiangong"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "tiangong@example.com"], cwd=repo, check=True)


def write_plan(repo: Path):
    plan_dir = repo / "docs" / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / "plan.md"
    plan_path.write_text("# Demo Plan\n- Task: DOCS-1 (docs)\n", encoding="utf-8")
    return plan_path


def git_commit(repo: Path, message: str):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, stdout=subprocess.DEVNULL)


def main():
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    parser = argparse.ArgumentParser(description="Tiangong minimal closed-loop verification")
    parser.add_argument("--project", default=f"tg-verify-mini-{now}")
    parser.add_argument("--repo", default=str(Path("/Users/xiaokai/clawd/tmp") / f"tg-verify-mini-{now}-repo"))
    args = parser.parse_args()

    project = args.project
    repo = Path(args.repo)

    # Prepare repo
    ensure_git_repo(repo)
    plan_path = write_plan(repo)
    git_commit(repo, "init plan")

    # Create team scaffold
    start_script = TOOL_ROOT / "scripts" / "start-project.sh"
    subprocess.run([str(start_script), project, str(repo), str(plan_path.relative_to(repo))], check=True)

    base_dir = TOOL_ROOT / "projects" / project / ".tiangong"
    sm = StateManager(base_dir)

    # PROJECT_STARTED
    start_id = run_id("start")
    sm.append_event({
        "type": "PROJECT_STARTED",
        "actor": "orchestrator",
        "project": project,
        "runId": start_id,
        "payload": {},
        "idempotencyKey": f"{project}:PROJECT_STARTED:{start_id}",
    })

    # TASKSPEC_PUBLISHED
    task_id = "DOCS-1"
    task_spec = {
        "taskId": task_id,
        "goal": "Create initial docs",
        "kind": "docs",
        "acceptance": ["README.md exists", "docs/architecture.md exists"],
        "dependencies": [],
        "contextFiles": ["docs/plans/plan.md"],
        "suggestedSkills": ["writer"],
        "preferredSkill": "writer",
        "fallbackSkills": ["default"],
        "riskLevel": "low",
    }
    sm.append_event({
        "type": "TASKSPEC_PUBLISHED",
        "actor": "pm",
        "project": project,
        "taskId": task_id,
        "payload": {"tasks": [task_spec]},
        "idempotencyKey": f"{project}:{task_id}:TASKSPEC_PUBLISHED",
    })

    # TASK_SKILL_SET (human)
    sm.append_event({
        "type": "TASK_SKILL_SET",
        "actor": "human",
        "project": project,
        "taskId": task_id,
        "payload": {"chosenSkill": "writer", "decisionSeq": 1},
        "idempotencyKey": f"{project}:{task_id}:TASK_SKILL_SET:1",
    })

    # WORKER_RUN_INTENT + STARTED
    run_id_val = run_id("r")
    sm.append_event({
        "type": "WORKER_RUN_INTENT",
        "actor": "orchestrator",
        "project": project,
        "taskId": task_id,
        "runId": run_id_val,
        "payload": {"reason": "verify"},
        "idempotencyKey": f"{project}:{task_id}:{run_id_val}:WORKER_RUN_INTENT",
    })
    sm.append_event({
        "type": "WORKER_RUN_STARTED",
        "actor": "orchestrator",
        "project": project,
        "taskId": task_id,
        "runId": run_id_val,
        "payload": {},
        "idempotencyKey": f"{project}:{task_id}:{run_id_val}:WORKER_RUN_STARTED",
    })

    # Simulate worker output
    (repo / "README.md").write_text("# tg-verify-mini\n\nVerification repo for Tiangong.\n", encoding="utf-8")
    (repo / "docs" / "architecture.md").write_text("# Architecture\n\nMinimal docs for verification.\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md", "docs/architecture.md"], cwd=repo, check=True)

    patch_path = base_dir / "evidence" / task_id / f"{run_id_val}.patch"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch = subprocess.check_output(["git", "diff", "--cached"], cwd=repo)
    patch_path.write_bytes(patch)

    changed = subprocess.check_output(["git", "diff", "--name-only", "--cached"], cwd=repo).decode("utf-8").splitlines()

    evidence_path = base_dir / "evidence" / task_id / f"{run_id_val}.md"
    evidence_path.write_text(
        "# Evidence\n\n- Files: README.md, docs/architecture.md\n\n## Commands\n```bash\n"
        "git diff --cached\n"
        "```\n",
        encoding="utf-8",
    )

    evidence_digest = sha256_file(evidence_path)
    patch_digest = sha256_file(patch_path)

    sm.append_event({
        "type": "EVIDENCE_SUBMITTED",
        "actor": "orchestrator",
        "project": project,
        "taskId": task_id,
        "runId": run_id_val,
        "payload": {
            "evidencePath": str(Path("evidence") / task_id / f"{run_id_val}.md"),
            "patchPath": str(Path("evidence") / task_id / f"{run_id_val}.patch"),
            "evidenceDigest": evidence_digest,
            "patchDigest": patch_digest,
            "pathSafety": {
                "pwd": str(repo),
                "repoRoot": str(repo),
                "changedFiles": changed,
            },
        },
        "idempotencyKey": f"{project}:{task_id}:{run_id_val}:EVIDENCE_SUBMITTED",
    })

    # Watchdog verdict PASS
    sm.append_event({
        "type": "WATCHDOG_VERDICT",
        "actor": "watchdog",
        "project": project,
        "taskId": task_id,
        "runId": run_id_val,
        "payload": {"verdict": "PASS", "reasons": [], "suggestedActions": []},
        "idempotencyKey": f"{project}:{task_id}:{run_id_val}:WATCHDOG_VERDICT",
    })

    # Worker completed + close + finish
    sm.append_event({
        "type": "WORKER_RUN_COMPLETED",
        "actor": "orchestrator",
        "project": project,
        "taskId": task_id,
        "runId": run_id_val,
        "payload": {"result": "success"},
        "idempotencyKey": f"{project}:{task_id}:{run_id_val}:WORKER_RUN_COMPLETED",
    })
    sm.append_event({
        "type": "RUN_CLOSED",
        "actor": "orchestrator",
        "project": project,
        "taskId": task_id,
        "runId": run_id_val,
        "payload": {"closeReason": "completed_with_pass", "verdictEventId": None},
        "idempotencyKey": f"{project}:{task_id}:{run_id_val}:RUN_CLOSED",
    })
    sm.append_event({
        "type": "PROJECT_FINISHED",
        "actor": "orchestrator",
        "project": project,
        "runId": start_id,
        "payload": {},
        "idempotencyKey": f"{project}:PROJECT_FINISHED:{start_id}",
    })

    result = reduce_events(base_dir)
    status = result.status

    print("OK: verify complete")
    print(json.dumps({
        "project": project,
        "repo": str(repo),
        "status": status.get("project"),
        "task": status.get("tasks"),
        "events": (base_dir / "audit" / "events.ndjson").as_posix(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
