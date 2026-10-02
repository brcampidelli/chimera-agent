#!/usr/bin/env bash
# The full Python gate, run in WSL on a copy of a working tree, installed from uv.lock as CI does.
#
#   wsl bash scripts/gate_wsl.sh /mnt/c/<path-to-a-worktree> [/tmp/<dest>]
#
# Why a copy: rsync over /mnt/c into /tmp keeps the run off the Windows file system (and off a tree
# someone is still editing). Why the lock: `uv pip install -e` resolves the NEWEST of everything,
# while CI installs `uv.lock`. On 2026-09-27 that picked up litellm 1.103.0, released hours earlier,
# and a test that holds for the locked 1.99.0 failed: a dependency drift reported as a regression.
# From Git Bash, launch it with MSYS_NO_PATHCONV=1 so the /mnt/c path reaches WSL untouched, and pass
# the file through `tr -d '\r'` first if it was checked out with CRLF.
set -u
SRC="${1:-}"; DST="${2:-/tmp/chimera-gate}"
case "$SRC" in /mnt/?/?*) ;; *) echo "refused source: [$SRC] (expected /mnt/<drive>/...)"; exit 9;; esac
case "$DST" in /tmp/?*) ;; *) echo "refused destination: [$DST] (must be under /tmp)"; exit 9;; esac
export PATH="$HOME/.local/bin:$PATH"
rm -rf "$DST"; mkdir -p "$DST"
# Exclude state and build output: .chimera is an agent's own data and has faked a regression before.
rsync -a --exclude .git --exclude .venv --exclude .venv-wsl --exclude node_modules --exclude '__pycache__' \
  --exclude '*.pyc' --exclude 'apps/desktop/dist' --exclude 'bench/*/results*' \
  --exclude 'target' --exclude '.pytest_cache' --exclude '.mypy_cache' --exclude '.ruff_cache' \
  --exclude '.chimera' --exclude '.claude' --exclude 'arxsub' \
  "$SRC/" "$DST/"
cd "$DST" || exit 9
# .gitignore only applies inside a repository; without it ruff lints the vendored desktop code.
git init -q .
mkdir -p apps/desktop/dist && touch apps/desktop/dist/.placeholder
uv sync -q --frozen --extra dev --extra desktop || { echo "INSTALL FAILED"; exit 8; }
. .venv/bin/activate
python -c "import importlib.metadata as m; print('litellm', m.version('litellm'))"
echo "== ruff"; ruff check . | tail -2
echo "== mypy"; mypy chimera | tail -1
echo "== pytest"; python -m pytest -q -p no:cacheprovider -rf 2>&1 | grep -E "^FAILED|passed|failed" | tail -30
