#!/usr/bin/env bash
# Planner A/B arm wrapper (generic_cli `command`), P1-01. One chimera solve of one task, model and arm.
# Same shape as bench/harness_bench/hb_solve.sh; the differences are the ones PREREGISTRATION.md 2
# declares: the model is an argument, the per-solve cap stays 2.0 (Amendment 1), and homes/logs live under
# p101-* so they cannot collide with another bench using the same harness checkout.
#
# args: TASK_ID PROMPT_FILE WORKSPACE HARNESS_ID MODEL [factor flags...]
set -u
task_id="$1"; prompt_file="$2"; ws="$3"; hid="$4"; model="$5"; shift 5
flags=("$@")

log="$HOME/p101-logs/${task_id}-${hid}.log"
home="$HOME/p101-homes/${task_id}-${hid}"
mkdir -p "$home" "$HOME/p101-logs"
# Not cleared here: a multi-round task (011 = 5 rounds) calls this once per round and each solve
# appends a receipt; the DRIVER clears the home once per solve.

export CHIMERA_HOME="$home"
export CHIMERA_HOST_EXEC=allow

echo "=== start=$(date +%s) task=$task_id hid=$hid model=$model flags=[${flags[*]}] ===" >> "$log"

"$HOME/p101-venv/bin/python" -m chimera.cli.main solve "$(cat "$prompt_file")" \
  -w "$ws" -m "$model" \
  --max-attempts 1 --max-steps 120 \
  --no-remember --no-collect --no-evolve-skills --no-manager \
  --keep-workspace --max-usd 2.0 \
  "${flags[@]}" >> "$log" 2>&1
rc=$?

echo "=== rc=$rc end=$(date +%s) task=$task_id hid=$hid ===" >> "$log"
exit $rc
