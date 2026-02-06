#!/bin/bash
# update-status.sh
# 用法: ./update-status.sh --task <id> --state <state>

TASK_ID=""
STATE=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --task) TASK_ID="$2"; shift; shift ;;
    --state) STATE="$2"; shift; shift ;;
    *) shift ;;
  esac
done

if [ -z "$TASK_ID" ] || [ -z "$STATE" ]; then
    echo "Usage: $0 --task <id> --state <state>"
    exit 1
fi

# 简单的 MVP 实现：追加日志到 status.log (避免复杂的 JSON 编辑)
# 实际生产环境应使用 jq 更新 status.json
echo "$(date) [UPDATE] Task: $TASK_ID -> $STATE" >> status.log
echo "Updated status for $TASK_ID to $STATE"
