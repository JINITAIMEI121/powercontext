#!/usr/bin/env bash
# End-to-end verification sample for the SDD context module.
# Usage: ./run_demo.sh [--provider minimax|mock]
set -euo pipefail
cd "$(dirname "$0")"

PY=${PY:-python3}
TASK=${TASK:-"给订单查询增加 since/until 日期区间筛选，since 大于 until 时抛 ValueError，保持原有顺序不变"}
ARGS=("$@")

echo "### 0. reset demo project to baseline"
(cd demo_project && git checkout -- . 2>/dev/null || true)
rm -rf demo_project/.sdd-runs

echo "### 1. init embedded seekdb + ingest artefacts"
$PY -m sddctx init
$PY -m sddctx ingest --stats

echo "### 2. development context selected for the task"
$PY -m sddctx context "$TASK" --task-id order-date-range

echo "### 3. run the task (before/after tests, model edits, persisted state)"
$PY -m sddctx run "$TASK" --task-id order-date-range "${ARGS[@]}"

echo "### 4. state read back from a fresh process"
$PY -m sddctx status --task-id order-date-range

echo "### 5. follow-up run reuses the persisted state"
$PY -m sddctx follow-up "$TASK" --task-id order-date-range "${ARGS[@]}"
