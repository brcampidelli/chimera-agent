#!/usr/bin/env bash
# Harness-Bench arm wrapper for B4b (study 22 phase 6) — B4's wrapper with its own venv — one chimera solve of one task under one arm.
#
# Same shape as `bench/harness_bench/hb_solve.sh` (start/rc/end epochs INTO the log, chimera's real
# exit code propagated), with two deliberate differences:
#
#   * the venv is `hb-venv-b4`, installed editable from the CLONE this experiment's code lives in —
#     the main working tree has another agent's uncommitted edits in it, and an arm built from a
#     tree someone is editing is not a ruler (§2aa);
#   * the per-arm flags carry `--tool-router MODEL` on the treated arm and nothing on the control,
#     so the ONLY difference between the two arms is the router.
#
# args: TASK_ID PROMPT_FILE WORKSPACE HARNESS_ID EXECUTOR_MODEL [extra flags...]
set -u
task_id="$1"; prompt_file="$2"; ws="$3"; hid="$4"; executor="$5"; shift 5
flags=("$@")

log="$HOME/hb-logs/${task_id}-${hid}.log"
home="$HOME/hb-homes/${task_id}-${hid}"
mkdir -p "$home" "$HOME/hb-logs"

export CHIMERA_HOME="$home"
export CHIMERA_HOST_EXEC=allow

{
  echo "=== start=$(date +%s) task=$task_id hid=$hid model=$executor flags=[${flags[*]}] ==="
} >> "$log"

# `python -m` puts the CURRENT DIRECTORY first on sys.path, and a `wsl` launched from Windows starts in
# the Windows working directory — which, on this machine, is the main Chimera tree someone else edits.
# The import then silently runs THAT code instead of the frozen ruler. Found in setup (study 22 phase 6):
# the venv listed the frozen worktree while `import chimera` resolved to the main tree. So the solve runs
# from $HOME, and the log records which tree answered.
cd "$HOME" || exit 2
echo "=== chimera=$("$HOME/hb-venv-b4b/bin/python" -c 'import chimera, os; print(os.path.dirname(chimera.__file__))') ===" >> "$log"

"$HOME/hb-venv-b4b/bin/python" -m chimera.cli.main solve "$(cat "$prompt_file")" \
  -w "$ws" -m "$executor" \
  --max-attempts 1 --max-steps 120 \
  --no-remember --no-collect --no-evolve-skills --no-manager \
  --keep-workspace --max-usd 2.0 \
  "${flags[@]}" >> "$log" 2>&1
rc=$?

echo "=== rc=$rc end=$(date +%s) task=$task_id hid=$hid ===" >> "$log"
exit $rc
