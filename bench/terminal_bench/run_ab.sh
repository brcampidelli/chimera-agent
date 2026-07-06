#!/usr/bin/env bash
# Two-arm OFFICIAL Terminal-Bench A/B on a small task subset, with a disk guard + cleanup.
#   baseline = a neutral built-in TB agent (no Chimera)
#   chimera  = our agent (installs the chimera wheel into each task container, then `chimera solve`)
# Both on the same task ids + model; per-task pass/fail -> `chimera bench-compare`.
#
# Run on a DISPOSABLE box only. Set the version-specific knobs below for your terminal-bench.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$HERE/.venv"
RESULTS="$HERE/results"; mkdir -p "$RESULTS"

# --- config (adjust for your environment) -------------------------------------------------
MODEL="${MODEL:-openrouter/deepseek/deepseek-chat-v3.1}"
TASK_IDS="${TASK_IDS:?set TASK_IDS to a comma-separated list of real Terminal-Bench 2.0 task ids}"
BASELINE_AGENT="${BASELINE_AGENT:-terminus}"     # the neutral built-in agent name in your TB version
MIN_FREE_GB="${MIN_FREE_GB:-15}"                 # abort + clean up if free disk drops below this
: "${OPENROUTER_API_KEY:?set OPENROUTER_API_KEY}"

WHEEL="$(ls -1 "$HERE"/dist/chimera_agent-*.whl 2>/dev/null | head -1 || true)"
[ -n "$WHEEL" ] || { echo "ERROR: no wheel in $HERE/dist — run setup.sh first."; exit 1; }
TB="$VENV/bin/tb"   # the terminal-bench CLI; confirm flags with: $TB run --help

# --- safety -------------------------------------------------------------------------------
cleanup() {
  echo "== cleanup: pruning stopped TB containers + dangling images =="
  docker container prune -f  >/dev/null 2>&1 || true
  docker image prune -f      >/dev/null 2>&1 || true
}
trap cleanup EXIT

disk_guard() {
  local free_gb; free_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
  echo "   [disk guard] ${free_gb}G free (floor ${MIN_FREE_GB}G)"
  if [ "${free_gb:-0}" -lt "$MIN_FREE_GB" ]; then
    echo "!! free disk below floor — aborting to protect the box."
    exit 2
  fi
}

tb_task_args() { local ids="$1"; printf ' --task-id %s' ${ids//,/ }; }

# --- Arm A: baseline (neutral agent) ------------------------------------------------------
disk_guard
echo "== ARM A — baseline ($BASELINE_AGENT) on: $TASK_IDS =="
# NOTE: confirm these flags against your terminal-bench (`$TB run --help`). The knobs that vary
# across versions are: agent selection, per-task selection, and the output path.
"$TB" run --agent "$BASELINE_AGENT" --model "$MODEL" \
    $(tb_task_args "$TASK_IDS") --output-path "$RESULTS/baseline" || true
disk_guard

# --- Arm B: chimera (installs the wheel in-container, then solves) -------------------------
echo "== ARM B — chimera on: $TASK_IDS =="
# The agent lives in chimera_agent.py in THIS dir; run tb from here so the import path resolves.
# CHIMERA_TB_WHEEL is the CONTAINER-side path — how the wheel is mounted/copied in is TB-version
# specific (agent-dir mount). If your TB mounts the agent dir at /agent, the default matches;
# otherwise copy $WHEEL into the container and set CHIMERA_TB_WHEEL to that path.
( cd "$HERE" && \
  CHIMERA_TB_MODEL="$MODEL" CHIMERA_TB_WHEEL="${CHIMERA_TB_WHEEL:-/agent/$(basename "$WHEEL")}" \
  "$TB" run --agent-import-path "chimera_agent:ChimeraAgent" --model "$MODEL" \
      $(tb_task_args "$TASK_IDS") --output-path "$RESULTS/chimera" ) || true
disk_guard

# --- collect + honest A/B -----------------------------------------------------------------
echo "== extract per-task pass/fail + compare =="
"$VENV/bin/python" "$HERE/collect.py" "$RESULTS/baseline" "$RESULTS/chimera" "$RESULTS"
"$VENV/bin/chimera" bench-compare "$RESULTS/baseline.json" "$RESULTS/chimera.json" \
    --baseline-name "$BASELINE_AGENT" --treatment-name chimera
