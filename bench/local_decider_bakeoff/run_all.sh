#!/usr/bin/env bash
# The whole bake-off, serially (see RUN.md): each arm starts its own server, waits for health, runs
# JevBench + governance registered + urgency4, stops its server; a failing arm does not stop the next.
# Then the registered readout. Runs from any directory under Git Bash: bash run_all.sh
set -uo pipefail
SP="C:/Users/brcam/AppData/Local/Temp/claude/C--Users-brcam-Desktop-Desenvolvendo-Projetos-Agent-AI/113de395-42b3-45ce-a32b-def6bb191e16/scratchpad"
WT="$SP/wt/k-bakeoff"
MAIN="C:/Users/brcam/Desktop/Desenvolvendo Projetos/Agent AI"
LOG="$SP/bakeoff/logs"
mkdir -p "$LOG"
overall=0
for arm in clef-q4 intern-2b eikos-4b; do
  bash "$WT/bench/local_decider_bakeoff/run_arm.sh" "$arm" 2>&1 | tee "$LOG/run-$arm.log"
  status=${PIPESTATUS[0]}
  echo "$arm $status" >> "$LOG/exit-codes.txt"
  [ "$status" -ne 0 ] && overall=1
done
cd "$MAIN" || exit 2
PYTHONUTF8=1 PYTHONPATH="$WT" uv run --extra dev python "$WT/bench/local_decider_bakeoff/verdict.py" --governance-reports \
  > "$WT/bench/local_decider_bakeoff/report.md" 2> "$LOG/verdict.err"
echo "[report] $WT/bench/local_decider_bakeoff/report.md (exit codes in $LOG/exit-codes.txt)"
exit $overall
