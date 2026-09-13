#!/usr/bin/env bash
# Launch the full factorial run in WSL. Run as `bash ~/hb-launch.sh` — a script file, so the .env
# loader and the driver invocation carry no nested `wsl bash -lc "..."` quoting (which mangled the
# sed/redirection and left the key unset). The loader is the CLAUDE.md-sanctioned form: export only
# clean KEY=value lines, CR stripped.
set -u
REPO="/mnt/c/Users/brcam/Desktop/Desenvolvendo Projetos/Agent AI"
cd "$REPO" || { echo "no repo at $REPO"; exit 1; }
[ -f .env ] || { echo ".env not found in $REPO"; exit 1; }
eval "$(tr -d '\r' < .env | grep -E '^[A-Za-z_]+=' | sed 's/^/export /')"
if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  echo "OPENROUTER_API_KEY not set after loading .env — abort"
  exit 1
fi
echo "key ok (len ${#OPENROUTER_API_KEY}); launching $(date -u +%FT%TZ)"
# Default to the full run; pass args (e.g. --only-tasks/--only-arms) to drive a subset re-run.
if [ "$#" -eq 0 ]; then set -- --concurrency 6 --max-usd 400; fi
exec "$HOME/hb-venv/bin/python" -u "$HOME/hb-run_factorial.py" "$@"
