#!/bin/bash
# report-blocker.sh
# 用法: ./report-blocker.sh --task <id> --reason "..."

TASK_ID=""
REASON=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --task) TASK_ID="$2"; shift; shift ;;
    --reason) REASON="$2"; shift; shift ;;
    *) shift ;;
  esac
done

if [ -z "$TASK_ID" ] || [ -z "$REASON" ]; then
    echo "Usage: $0 --task <id> --reason '...'"
    exit 1
fi

echo "$(date) [BLOCKER] Task: $TASK_ID Reason: $REASON" >> status.log
echo "Reported blocker for $TASK_ID"
