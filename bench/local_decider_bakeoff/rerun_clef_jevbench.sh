#!/usr/bin/env bash
# Amendment 1 (PREREGISTRATION.md): clef-q4's JevBench-231 rerun with -b/-ub 8192 (ladder 6144, 4608),
# the two reuse checks, and — only if one fails — governance registered + urgency4 rerun under the new
# configuration. Then the registered readout into report.md. Runs from any directory under Git Bash.
# Exit codes as run_arm.sh, plus 7 when requests halt (smoke halt, or > 2% of JevBench).
set -uo pipefail
SP="C:/Users/brcam/AppData/Local/Temp/claude/C--Users-brcam-Desktop-Desenvolvendo-Projetos-Agent-AI/113de395-42b3-45ce-a32b-def6bb191e16/scratchpad"
WT="$SP/wt/k-bakeoff"
MAIN="C:/Users/brcam/Desktop/Desenvolvendo Projetos/Agent AI"
LOG="$SP/bakeoff/logs"
mkdir -p "$LOG"
cd "$MAIN" || exit 2
export PYTHONUTF8=1 PYTHONPATH="$WT"
echo "[clef-q4 rerun] start $(date +%T)"
uv run --extra dev python "$WT/bench/local_decider_bakeoff/run_model.py" --arm clef-q4 --clef-jevbench-rerun \
  --bakeoff "$SP/bakeoff" --jevbench "$SP/jevbench-clone" --llama-dir "$SP/s1q/llama" \
  --intern-python "$SP/intern/.venv/Scripts/python.exe" --intern-vendor "$SP/intern/vendor" "$@" 2>&1 | tee "$LOG/rerun-clef-q4.log"
status=${PIPESTATUS[0]}
echo "clef-q4-rerun $status" >> "$LOG/exit-codes.txt"
uv run --extra dev python "$WT/bench/local_decider_bakeoff/verdict.py" --governance-reports \
  > "$WT/bench/local_decider_bakeoff/report.md" 2> "$LOG/verdict.err"
echo "[clef-q4 rerun] exit $status $(date +%T) · report $WT/bench/local_decider_bakeoff/report.md"
exit $status
