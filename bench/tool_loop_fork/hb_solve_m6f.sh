#!/usr/bin/env bash
# Harness-Bench arm wrapper for the M6 fork (study 24) — M6's wrapper with two additions:
#
#   * every solve escalates on the first trip AND copies the workspace at that moment
#     (`--snapshot-at-tool-loop`) to ~/hb-snapshots/<task>-<hid>, which is what stopping would have
#     left: the breaker's own ending asks for an answer with no tools;
#   * the workspace path goes into the log, so the grader can re-grade the escalated workspace with the
#     same function it uses on the snapshot.
#
# args: TASK_ID PROMPT_FILE WORKSPACE HARNESS_ID EXECUTOR_MODEL ESCALATE_TO
set -u
task_id="$1"; prompt_file="$2"; ws="$3"; hid="$4"; executor="$5"; escalate_to="$6"

log="$HOME/hb-logs/${task_id}-${hid}.log"
home="$HOME/hb-homes/${task_id}-${hid}"
snap="$HOME/hb-snapshots/${task_id}-${hid}"
mkdir -p "$home" "$HOME/hb-logs" "$HOME/hb-snapshots"

export CHIMERA_HOME="$home"
export CHIMERA_HOST_EXEC=allow

{
  echo "=== start=$(date +%s) task=$task_id hid=$hid model=$executor escalate_to=$escalate_to ==="
  echo "=== workspace=$ws ==="
  echo "=== snapshot=$snap ==="
} >> "$log"

# Run from $HOME so `python -m` cannot import a Chimera from the Windows working directory (study 22).
cd "$HOME" || exit 2
echo "=== chimera=$("$HOME/hb-venv-m6f/bin/python" -c 'import chimera, os; print(os.path.dirname(chimera.__file__))') ===" >> "$log"

"$HOME/hb-venv-m6f/bin/python" -m chimera.cli.main solve "$(cat "$prompt_file")" \
  -w "$ws" -m "$executor" \
  --max-attempts 1 --max-steps 120 \
  --no-remember --no-collect --no-evolve-skills --no-manager \
  --keep-workspace --max-usd 2.0 \
  --escalate-on-tool-loop "$escalate_to" --snapshot-at-tool-loop "$snap" >> "$log" 2>&1
rc=$?

echo "=== rc=$rc end=$(date +%s) task=$task_id hid=$hid ===" >> "$log"
exit $rc
