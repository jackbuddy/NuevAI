#!/usr/bin/env bash
# Runs the queued prompts one after another, unattended. Usage:
#   prompts/run_queue.sh            # all prompts 01..12
#   prompts/run_queue.sh 01 03 05   # chosen prompts, in that order
# Each prompt = standing rules + that prompt, run as a fresh headless Claude Code session.
set -u
cd "$(dirname "$0")/.."
BUDGET="${BUDGET:-15}"       # max USD per prompt (only applies if you pay per token; ignore on a subscription)
MODE="${MODE:-auto}"         # permission mode: auto | acceptEdits | dontAsk
ids=("$@"); [ ${#ids[@]} -eq 0 ] && ids=(01 02 03 04 05 06 07 08 09 10 11 12)
mkdir -p data/queue_logs
for id in "${ids[@]}"; do
  f=$(ls prompts/${id}_*.md 2>/dev/null | head -1) || true
  [ -z "$f" ] && { echo "no prompt $id"; continue; }
  echo "=== $(date '+%H:%M:%S') running $f"
  { cat prompts/00_STANDING_RULES.md; echo; echo "# TASK"; cat "$f"; } | \
    claude -p --permission-mode "$MODE" --max-budget-usd "$BUDGET" \
    > "data/queue_logs/$(basename "$f" .md).log" 2>&1
  echo "    exit $? -> data/queue_logs/$(basename "$f" .md).log"
done
echo "done. read QUEUE_SUMMARY.md / QUEUE_LOG.md / NEEDS_USER.md"
