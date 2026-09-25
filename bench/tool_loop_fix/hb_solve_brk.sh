#!/usr/bin/env bash
# Harness-Bench arm wrapper for the breaker re-measurement (study 24) — M6's wrapper with the venv chosen
# per arm: the two arms differ ONLY in which frozen Chimera they import, and the two frozen trees differ
# only in `chimera/core/tool_loop.py` (and its tests).
#
# args: TASK_ID PROMPT_FILE WORKSPACE HARNESS_ID EXECUTOR_MODEL VENV_NAME
set -u
task_id="$1"; prompt_file="$2"; ws="$3"; hid="$4"; executor="$5"; venv="$6"
case "$venv" in hb-venv-brk-legacy|hb-venv-brk-fixed) ;; *) echo "unknown venv [$venv]"; exit 9;; esac

log="$HOME/hb-logs/${task_id}-${hid}.log"
home="$HOME/hb-homes/${task_id}-${hid}"
mkdir -p "$home" "$HOME/hb-logs"

export CHIMERA_HOME="$home"
export CHIMERA_HOST_EXEC=allow

{
  echo "=== start=$(date +%s) task=$task_id hid=$hid model=$executor venv=$venv ==="
  echo "=== workspace=$ws ==="
} >> "$log"

# Run from $HOME so `python -m` cannot import a Chimera from the Windows working directory (study 22).
cd "$HOME" || exit 2
echo "=== chimera=$("$HOME/$venv/bin/python" -c 'import chimera, os; print(os.path.dirname(chimera.__file__))') ===" >> "$log"

"$HOME/$venv/bin/python" -m chimera.cli.main solve "$(cat "$prompt_file")" \
  -w "$ws" -m "$executor" \
  --max-attempts 1 --max-steps 120 \
  --no-remember --no-collect --no-evolve-skills --no-manager \
  --keep-workspace --max-usd 2.0 >> "$log" 2>&1
rc=$?

echo "=== rc=$rc end=$(date +%s) task=$task_id hid=$hid ===" >> "$log"
exit $rc
