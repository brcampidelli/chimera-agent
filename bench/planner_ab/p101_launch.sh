#!/usr/bin/env bash
# Launch the planner A/B driver in WSL: wsl bash -l bench/planner_ab/p101_launch.sh --tag glm --phase pilot
# Loads only clean KEY=value lines of the main checkout's .env (CR stripped); never prints a value.
set -u
# Run under a LOGIN shell (wsl bash -l ...): the oracles call `pytest` and `node`, which live in ~/.local/bin.
export PATH="$HOME/.local/bin:$HOME/hb-venv/bin:$PATH"
REPO="/mnt/c/Users/brcam/Desktop/Desenvolvendo Projetos/Agent AI"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$REPO/.env" ] || { echo ".env not found"; exit 1; }
eval "$(tr -d '\r' < "$REPO/.env" | grep -E '^[A-Za-z_]+=' | sed 's/^/export /')"
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "OPENROUTER_API_KEY not set"; exit 1; }
install -m 755 "$HERE/p101_solve.sh" "$HOME/p101-solve.sh"
exec "$HOME/hb-venv/bin/python" -u "$HERE/run_ab.py" "$@"
