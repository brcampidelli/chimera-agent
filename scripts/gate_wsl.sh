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
# Exclude state and build output: .chimera is an agent's own data and has faked a regression
# before. bench results are COPIED, not excluded: the meta-tests added in #781 read them (the
# census reads probe.json and harness_bench's one file), CI checks the tree out WITH them, and
# the whole set is 64 MB — an exclusion that saved two seconds of rsync cost a day of whack-a-mole
# includes when a test reached for one more file.
rsync -a \
  --exclude .git --exclude .venv --exclude .venv-wsl --exclude node_modules --exclude '__pycache__' \
  --exclude '*.pyc' --exclude 'apps/desktop/dist' \
  --exclude 'target' --exclude '.pytest_cache' --exclude '.mypy_cache' --exclude '.ruff_cache' \
  --exclude '.chimera' --exclude '.claude' --exclude 'arxsub' \
  "$SRC/" "$DST/"
cd "$DST" || exit 9
# .gitignore only applies inside a repository; without it ruff lints the vendored desktop code.
git init -q .
# Lend the real repository's objects and refs to this fresh one, read-only. The meta-tests added in
# #781 read HISTORY — `git show <fingerprinted-commit>:<file>` — and a copy with no history failed
# all 24 of them, red for a reason the checkout explains rather than the assertions. With the
# alternates pointer the commits exist here and the tests run for real; the real repository is
# never written to. (A worktree's .git is a pointer file, so it cannot simply be rsynced.)
if [ -f "$SRC/.git" ]; then
  # A worktree's pointer names its git dir as a WINDOWS path (`C:/...`), which WSL git cannot
  # use; rewrite the drive to its /mnt mount before walking two levels up to the real .git.
  _gitdir="$(tr -d '\r' < "$SRC/.git" | sed 's/^gitdir: *//')"
  case "$_gitdir" in
    [A-Za-z]:/*) _gitdir="/mnt/$(echo "${_gitdir%%:*}" | tr 'A-Z' 'a-z')${_gitdir#?:}" ;;
  esac
  _real_git="$(dirname "$(dirname "$_gitdir")")"
else
  _real_git="$SRC/.git"
  _gitdir="$_real_git"
fi
if [ -d "$_real_git/objects" ]; then
  mkdir -p .git/objects/info .git/refs/remotes/origin
  echo "$_real_git/objects" > .git/objects/info/alternates
  # The refs the meta-tests read by name (`refs/remotes/origin/<branch>`), copied as loose refs.
  # A branch name carries slashes (`origin/feat/x`), so each ref's parent is made before the write.
  git --git-dir="$_real_git" for-each-ref --format='%(refname) %(objectname)' 'refs/remotes/origin/*' \
    | while read -r ref sha; do mkdir -p ".git/$(dirname "$ref")" && echo "$sha" > ".git/$ref"; done
  # And the branch HEAD points at, so `git rev-parse HEAD` answers and the tests can tell this
  # state from a full clone — and `merge-base --is-ancestor <commit> HEAD` works, which is how
  # the meta-tests decide whether a fingerprinted commit is on main.
  # HEAD is read from the WORKTREE's git dir, not the main repository's: each worktree has its
  # own, and the main checkout may be on an older branch — which would make the copy's HEAD an
  # ancestor that cannot reach the commits the meta-tests check.
  _branch="$(git --git-dir="$_gitdir" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  _tip="$(git --git-dir="$_gitdir" rev-parse HEAD 2>/dev/null || true)"
  if [ -n "$_branch" ] && [ -n "$_tip" ]; then
    # `feat/cron-kill-and-provenance` is a nested ref: its parent directory first.
    mkdir -p ".git/refs/heads/$(dirname "$_branch")"
    echo "ref: refs/heads/$_branch" > .git/HEAD
    echo "$_tip" > ".git/refs/heads/$_branch"
  fi
fi
mkdir -p apps/desktop/dist && touch apps/desktop/dist/.placeholder
uv sync -q --frozen --extra dev --extra desktop || { echo "INSTALL FAILED"; exit 8; }
. .venv/bin/activate
python -c "import importlib.metadata as m; print('litellm', m.version('litellm'))"
echo "== ruff"; ruff check . | tail -2
echo "== mypy"; mypy chimera | tail -1
echo "== pytest"; python -m pytest -q -p no:cacheprovider -rf 2>&1 | grep -E "^FAILED|passed|failed" | tail -30
