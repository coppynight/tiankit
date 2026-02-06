#!/bin/bash

# Multi-Agent 项目启动脚本
set -e

GREEN='\033[0;32m'
NC='\033[0m'

if [ $# -lt 2 ]; then
    echo "用法: $0 <项目名> <项目根目录> [计划文件]"
    exit 1
fi

PROJECT_NAME="$1"
PROJECT_ROOT="$2"
PLAN_FILE="${3:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_ROOT="$(dirname "$SCRIPT_DIR")"
TEMPLATES_DIR="$TOOL_ROOT/templates"
PROJECT_DIR="$TOOL_ROOT/projects/$PROJECT_NAME"
TIANGONG_DIR="$PROJECT_DIR/.tiangong"

echo -e "${GREEN}启动项目: $PROJECT_NAME${NC}"

# 创建目录
mkdir -p "$PROJECT_DIR"
mkdir -p "$PROJECT_DIR/orchestrator"
mkdir -p "$PROJECT_DIR/watchdog"
mkdir -p "$PROJECT_DIR/pm"
mkdir -p "$PROJECT_DIR/worker"
mkdir -p "$PROJECT_DIR/reviewer"
mkdir -p "$PROJECT_DIR/docs"

# Tiangong layout (central)
mkdir -p "$TIANGONG_DIR/audit" "$TIANGONG_DIR/derived" "$TIANGONG_DIR/evidence"

# 复制模板
cp "$TEMPLATES_DIR/orchestrator/SYSTEM.md" "$PROJECT_DIR/orchestrator/"
cp "$TEMPLATES_DIR/watchdog/SYSTEM.md" "$PROJECT_DIR/watchdog/"
cp "$TEMPLATES_DIR/pm/SYSTEM.md" "$PROJECT_DIR/pm/"
cp "$TEMPLATES_DIR/worker/SYSTEM.md" "$PROJECT_DIR/worker/"
cp "$TEMPLATES_DIR/reviewer/SYSTEM.md" "$PROJECT_DIR/reviewer/"

# 生成 agent.yaml (Watchdog)
cat > "$PROJECT_DIR/watchdog/agent.yaml" <<EOF
name: "${PROJECT_NAME}-watchdog"
description: "Project Guardian for $PROJECT_NAME"
model:
  primary: google-antigravity/claude-opus-4-5-thinking
workspace: "$PROJECT_ROOT"
system: "SYSTEM.md"
channels: inherit
EOF

# 生成 agent.yaml (PM)
cat > "$PROJECT_DIR/pm/agent.yaml" <<EOF
name: "${PROJECT_NAME}-pm"
description: "Project Manager for $PROJECT_NAME"
model:
  primary: minimax-cn/MiniMax-M2.1
workspace: "$PROJECT_ROOT"
system: "SYSTEM.md"
channels: inherit
EOF

# 生成 agent.yaml (Worker)
cat > "$PROJECT_DIR/worker/agent.yaml" <<EOF
name: "${PROJECT_NAME}-worker"
description: "Worker for $PROJECT_NAME"
model:
  primary: google-antigravity/claude-sonnet-4-5
workspace: "$PROJECT_ROOT"
system: "SYSTEM.md"
channels: inherit
EOF

# 生成 agent.yaml (Reviewer)
cat > "$PROJECT_DIR/reviewer/agent.yaml" <<EOF
name: "${PROJECT_NAME}-reviewer"
description: "Reviewer for $PROJECT_NAME"
model:
  primary: google-antigravity/claude-opus-4-5-thinking
workspace: "$PROJECT_ROOT"
system: "SYSTEM.md"
channels: inherit
EOF

# Tiangong team.json
cat > "$TIANGONG_DIR/team.json" <<EOF
{
  "teamId": "$(uuidgen)",
  "project": "$PROJECT_NAME",
  "repo": null,
  "path": "$PROJECT_ROOT",
  "planPath": "${PLAN_FILE}",
  "createdAt": "$(date -u +"%Y-%m-%dT%H:%M:%S.%6NZ")",
  "defaults": {
    "skillMemory": {
      "coding": "",
      "build_test": "",
      "docs": "",
      "research": "",
      "ops": "",
      "design": "",
      "comms": ""
    },
    "codingCliPath": null
  },
  "labels": {
    "orchestrator": "tg:${PROJECT_NAME}:orchestrator",
    "pm": "tg:${PROJECT_NAME}:pm",
    "watchdog": "tg:${PROJECT_NAME}:watchdog"
  },
  "policies": {
    "projectRoot": "$PROJECT_ROOT",
    "denyPaths": ["~/.ssh", "/etc"],
    "denyCommands": ["rm -rf", "sudo", "dd", "mkfs"]
  }
}
EOF

# Tiangong registry.json (optional skill registry)
cat > "$TIANGONG_DIR/registry.json" <<EOF
{
  "skills": []
}
EOF

# Seed TEAM_CREATED event + initial status
python3 - <<PY
from pathlib import Path
import sys

base_dir = Path("$TIANGONG_DIR")
tool_root = Path("$TOOL_ROOT")
sys.path.insert(0, str(tool_root))

from core.state_manager import StateManager
from core.reducer import reduce_events
from core.state_manager import atomic_write_json

team = base_dir / "team.json"
project = "unknown"
if team.exists():
    try:
        import json
        project = json.loads(team.read_text(encoding="utf-8")).get("project") or project
    except Exception:
        pass

sm = StateManager(base_dir)
sm.append_event({
    "type": "TEAM_CREATED",
    "actor": "human",
    "project": project,
    "payload": {},
    "idempotencyKey": f"{project}:TEAM_CREATED",
})

result = reduce_events(base_dir)
atomic_write_json(base_dir / "status.json", result.status)
PY

# 复制计划
if [ -n "$PLAN_FILE" ] && [ -f "$PROJECT_ROOT/$PLAN_FILE" ]; then
    cp "$PROJECT_ROOT/$PLAN_FILE" "$PROJECT_DIR/docs/plan.md"
    echo "计划文件已复制"
fi

echo -e "${GREEN}项目初始化完成: $PROJECT_DIR${NC}"
