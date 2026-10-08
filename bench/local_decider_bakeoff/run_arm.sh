#!/usr/bin/env bash
# One arm of the local bake-off, end to end (see RUN.md). Usage: bash run_arm.sh clef-q4|intern-2b|eikos-4b [--smoke]
# Runs from any directory under Git Bash. Exit status is run_model.py's: 0 only when every guard passed,
# 3 GPU not free, 4 invalid probabilities, 5 server failed to start, 6 JevBench easy below 0.90.
set -uo pipefail
ARM="${1:?arm}"; shift
SP="C:/Users/brcam/AppData/Local/Temp/claude/C--Users-brcam-Desktop-Desenvolvendo-Projetos-Agent-AI/113de395-42b3-45ce-a32b-def6bb191e16/scratchpad"
WT="$SP/wt/k-bakeoff"
MAIN="C:/Users/brcam/Desktop/Desenvolvendo Projetos/Agent AI"
cd "$MAIN" || exit 2
export PYTHONUTF8=1 PYTHONPATH="$WT"
echo "[$ARM] start $(date +%T)"
uv run --extra dev python "$WT/bench/local_decider_bakeoff/run_model.py" --arm "$ARM" \
  --bakeoff "$SP/bakeoff" --jevbench "$SP/jevbench-clone" --llama-dir "$SP/s1q/llama" \
  --intern-python "$SP/intern/.venv/Scripts/python.exe" --intern-vendor "$SP/intern/vendor" "$@"
status=$?
echo "[$ARM] exit $status $(date +%T)"
exit $status
