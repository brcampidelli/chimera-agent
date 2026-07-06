#!/usr/bin/env bash
# Self-contained setup for the OFFICIAL Terminal-Bench A/B.
# Run on a DISPOSABLE box (throwaway VPS or a local machine with Docker) — NEVER a production host.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
VENV="$HERE/.venv"
MIN_FREE_GB="${MIN_FREE_GB:-25}"   # refuse to set up with less headroom than this

echo "== preflight =="
command -v docker >/dev/null 2>&1 || { echo "ERROR: Docker not found — install Docker first."; exit 1; }
docker info >/dev/null 2>&1 || { echo "ERROR: Docker daemon not running."; exit 1; }
py_ok=$(python3 -c 'import sys; print(1 if sys.version_info[:2] >= (3, 12) else 0)')
[ "$py_ok" = "1" ] || { echo "ERROR: need Python >= 3.12 (have $(python3 -V))."; exit 1; }

free_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
echo "free disk: ${free_gb}G (minimum ${MIN_FREE_GB}G)"
[ "${free_gb:-0}" -ge "$MIN_FREE_GB" ] || { echo "ERROR: not enough free disk. Aborting."; exit 1; }

echo "== venv + terminal-bench =="
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip build
# The benchmark harness. If the distribution name differs in your environment, adjust here.
"$VENV/bin/pip" install --quiet terminal-bench

echo "== build the chimera wheel (the treatment agent installs THIS inside each task container) =="
rm -rf "$HERE/dist"
( cd "$REPO_ROOT" && "$VENV/bin/python" -m build --wheel --outdir "$HERE/dist" )
wheel=$(ls -1 "$HERE"/dist/chimera_agent-*.whl 2>/dev/null | head -1 || true)
[ -n "$wheel" ] || { echo "ERROR: wheel build produced no chimera_agent-*.whl"; exit 1; }
echo "wheel: $wheel"

echo
echo "== setup OK =="
echo "Next: export OPENROUTER_API_KEY, MODEL, TASK_IDS, BASELINE_AGENT, then:"
echo "  bash $HERE/run_ab.sh"
