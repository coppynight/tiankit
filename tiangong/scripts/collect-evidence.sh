#!/bin/bash
# collect-evidence.sh <task_id>

TASK_ID="$1"
if [ -z "$TASK_ID" ]; then
    echo "Usage: $0 <task_id>"
    exit 1
fi

EVIDENCE_FILE="evidence-${TASK_ID}.json"

# 收集变更文件
CHANGED_FILES=$(git diff --name-only HEAD 2>/dev/null || echo "No git repo")

# 生成 JSON
cat > "$EVIDENCE_FILE" <<EOF
{
  "taskId": "$TASK_ID",
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "evidence": {
    "files": "$(echo $CHANGED_FILES | tr '\n' ',')",
    "system_info": "$(uname -a)"
  }
}
EOF

echo "Evidence collected: $EVIDENCE_FILE"
