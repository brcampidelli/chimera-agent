#!/usr/bin/env bash
# Read-only probe of the WSL environment Part 2 needs. Spends nothing, prints no secret.
set -u

echo "--- chimera binary ---"
ls -l /home/brcamp/hb-venv/bin/chimera 2>&1 | head -3
/home/brcamp/hb-venv/bin/chimera --version 2>&1 | head -3

echo "--- which repo is it editable on ---"
/home/brcamp/hb-venv/bin/python -c 'import chimera, pathlib; print(pathlib.Path(chimera.__file__).resolve().parent)' 2>&1 | head -3

echo "--- textual present ---"
/home/brcamp/hb-venv/bin/python -c 'import textual; print("textual", textual.__version__)' 2>&1 | head -3

echo "--- script(1) ---"
command -v script && script --version 2>&1 | head -1

echo "--- key present? (presence only, never the value) ---"
eval "$(tr -d '\r' < "/mnt/c/Users/brcam/Desktop/Desenvolvendo Projetos/Agent AI/.env" | grep -E '^[A-Za-z_]+=' | sed 's/^/export /')"
if [ -n "${OPENROUTER_API_KEY:-}" ]; then echo "OPENROUTER_API_KEY: present"; else echo "OPENROUTER_API_KEY: MISSING"; fi

echo "--- confirm.py state in the installed copy ---"
/home/brcamp/hb-venv/bin/python - <<'PY'
import inspect
from chimera.sandbox import confirm
print("PROMPT_TIMEOUT_SECONDS =", confirm.PROMPT_TIMEOUT_SECONDS)
print("_no_human_surface     =", confirm._no_human_surface)
src = inspect.getsource(confirm._human_can_answer)
print("_human_can_answer body:", " | ".join(l.strip() for l in src.splitlines() if l.strip())[:200])
PY

echo "--- does the tui declare no human? ---"
grep -rn "declare_no_human_here" /home/brcamp/hb-venv/lib/python3*/site-packages/chimera/ 2>/dev/null | head -5
echo "(and in the source tree)"
grep -rn "declare_no_human_here" "/mnt/c/Users/brcam/Desktop/Desenvolvendo Projetos/Agent AI/chimera/" 2>/dev/null | head -5
