#!/usr/bin/env bash
# Harness-Bench arm wrapper (generic_cli `command`). Runs one chimera solve of one task under one
# arm, records start/rc/end epochs INTO the per-solve log (never `; echo rc=$?` in the harness args —
# that made bash exit 0 and the harness recorded the echo's code, not chimera's), and propagates
# chimera's real exit code so the harness sees it.
#
# args: TASK_ID PROMPT_FILE WORKSPACE HARNESS_ID [factor flags...]
set -u
task_id="$1"; prompt_file="$2"; ws="$3"; hid="$4"; shift 4
flags=("$@")

log="$HOME/hb-logs/${task_id}-${hid}.log"
home="$HOME/hb-homes/${task_id}-${hid}"
mkdir -p "$home" "$HOME/hb-logs"
# NOTE: this wrapper does NOT clear the home. A multi-round task (e.g. 011 = 5 rounds) invokes this
# wrapper once per round, and each chimera solve APPENDS its receipt to runs.jsonl; clearing here
# would leave only the last round's receipt and lose the rounds' summed cost. The DRIVER clears the
# home once per solve (per run-task call) so re-runs stay clean.

export CHIMERA_HOME="$home"
export CHIMERA_HOST_EXEC=allow

{
  echo "=== start=$(date +%s) task=$task_id hid=$hid flags=[${flags[*]}] ==="
} >> "$log"

"$HOME/hb-venv/bin/python" -m chimera.cli.main solve "$(cat "$prompt_file")" \
  -w "$ws" -m openrouter/deepseek/deepseek-v3.2 \
  --max-attempts 1 --max-steps 120 \
  --no-remember --no-collect --no-evolve-skills --no-manager \
  --keep-workspace --max-usd 2.0 \
  "${flags[@]}" >> "$log" 2>&1
rc=$?

echo "=== rc=$rc end=$(date +%s) task=$task_id hid=$hid ===" >> "$log"
exit $rc
