#!/usr/bin/env bash
# Load the OpenRouter key from the repo's .env into THIS shell's environment — never printed,
# never logged — then hand off to run.py with whatever arguments were given.
#
#   bash bench/cache_confound/run.sh --check
#   bash bench/cache_confound/run.sh --grid --pin DeepSeek
set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
# The key lives in the MAIN checkout's .env (gitignored, so a worktree has none). Override with
# CHIMERA_ENV_FILE when running from somewhere else.
ENV_FILE="${CHIMERA_ENV_FILE:-/mnt/c/Users/brcam/Desktop/Desenvolvendo Projetos/Agent AI/.env}"
# shellcheck disable=SC2046
eval "$(tr -d '\r' < "$ENV_FILE" | grep -E '^[A-Za-z_]+=' | sed 's/^/export /')"
if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  echo "OPENROUTER_API_KEY is not set after loading .env — refusing to run" >&2
  exit 1
fi
exec "$HOME/hb-venv/bin/python" "$REPO/bench/cache_confound/run.py" "$@"
