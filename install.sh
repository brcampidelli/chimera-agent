#!/usr/bin/env bash
# Chimera Agent — quick install for a Linux/macOS/WSL host (bare metal, no Docker).
# From a clone:   ./install.sh
# One-liner:      curl -fsSL https://raw.githubusercontent.com/brcampidelli/chimera-agent/main/install.sh | bash
set -euo pipefail

REPO="https://github.com/brcampidelli/chimera-agent.git"
DIR="${CHIMERA_DIR:-$HOME/chimera-agent}"

# If we're not already inside the repo, clone it.
if [ ! -f pyproject.toml ] || ! grep -q 'name = "chimera-agent"' pyproject.toml 2>/dev/null; then
  echo "Cloning Chimera into $DIR ..."
  git clone --depth 1 "$REPO" "$DIR"
  cd "$DIR"
fi

command -v python3 >/dev/null 2>&1 || { echo "python3 (>=3.11) is required"; exit 1; }

echo "Creating virtualenv and installing (this pulls the deps) ..."
python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install -q --upgrade pip
pip install -q '.[messaging,mcp]'

[ -f .env ] || cp .env.example .env

cat <<'DONE'

Chimera installed. Next steps:
  1) edit .env and set a provider key   (e.g. CHIMERA_OPENROUTER_KEYS=sk-or-...)
  2) . .venv/bin/activate
  3) chimera doctor                      # verify configuration
  4) chimera serve --cron                # gateway + proactive cron daemon

For a server (systemd / Docker), see docs/deploy.md.
DONE
