#!/usr/bin/env bash
# One command for every step that needs the real database, in order, with no model spend:
# model answers are replayed only (CPGOS_LLM_MODE=replay; a missing recording stops that step
# instead of calling a paid API). Everything is written to local_runs/ (never committed), so the
# results can be read back without copying terminal output.
#
#   make real-run
set -u
cd "$(dirname "$0")/.."
mkdir -p local_runs
LOG="local_runs/real_run_$(date +%Y%m%d_%H%M%S).log"
STATUS="local_runs/latest_status.txt"
: > "$STATUS"
step() {   # step <name> <command...>
  local name="$1"; shift
  echo "=== $name: $*" | tee -a "$LOG"
  local t0=$SECONDS
  "$@" >>"$LOG" 2>&1
  local rc=$?
  printf '%-18s exit %-3s %4ss\n' "$name" "$rc" "$((SECONDS - t0))" | tee -a "$STATUS" "$LOG"
  return $rc
}
export CPGOS_LLM_MODE=replay
step db              bash scripts/dev_db.sh || { echo "Database did not start; see $LOG"; exit 1; }
step migrate         make migrate          || { echo "Migrations failed; see $LOG"; exit 1; }
step decide-docs     make decide-docs ROUND=5
step regress-docs    make regress-docs
step eval-recon      make eval-recon
step names-analysis  make names-analysis
step world-sidecar   make world-sidecar FRESH=1
step test            make test
echo "log: $LOG" | tee -a "$STATUS"
echo
cat "$STATUS"
