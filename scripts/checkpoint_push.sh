#!/bin/bash
# 探索の途中結果を一定間隔でコミット&pushする(コンテナが回収されても結果が残るように)。
#   bash scripts/checkpoint_push.sh [間隔秒=300]
cd "$(dirname "$0")/.."
while true; do
  git add tuning_log.jsonl tuning_center.json best_params.json majai/best_params.json 2>/dev/null
  if ! git diff --cached --quiet; then
    git commit -q -m "Tuning checkpoint $(date -u +%H:%M)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NutjbxWha8QnZYTahvf562" && \
    (git push -q origin HEAD 2>&1 || (sleep 5; git push -q origin HEAD 2>&1)) || true
  fi
  sleep "${1:-300}"
done
