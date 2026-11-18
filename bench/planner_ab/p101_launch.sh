#!/usr/bin/env bash
# Launch in WSL: wsl bash -l bench/planner_ab/p101_launch.sh --tag glm --phase pilot
set +x
set -eu
# Run under a login shell: the oracles call pytest and node, which live in ~/.local/bin.
export PATH="$HOME/.local/bin:$HOME/hb-venv/bin:$PATH"
REPO="/mnt/c/Users/brcam/Desktop/Desenvolvendo Projetos/Agent AI"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The checkout's .env is data. Only a literal OpenRouter key can enter the benchmark.
if [[ -z ${OPENROUTER_API_KEY:-} && -f "$REPO/.env" ]]; then
    key_assignment="^OPENROUTER_API_KEY=(['\"]?)([A-Za-z0-9_-]+)\\1$"
    while IFS= read -r line || [[ -n $line ]]; do
        if [[ ${line%$'\r'} =~ $key_assignment ]]; then
            OPENROUTER_API_KEY="${BASH_REMATCH[2]}"
        fi
    done < "$REPO/.env"
fi
[[ -n ${OPENROUTER_API_KEY:-} ]] || { echo "OPENROUTER_API_KEY not set" >&2; exit 1; }

# Keep shell/oracle essentials and the five controls from _legacy_defaults.py.
for name in $(compgen -e); do
    case "$name" in
        HOME|PATH|LANG|LC_ALL|LC_CTYPE|OPENROUTER_API_KEY|CHIMERA_DEFER_TOOLS|CHIMERA_SKILL_CARDS|\
        CHIMERA_CODE_CLOSE_LOOP|CHIMERA_PULL_REQUESTS|CHIMERA_CHAT_INBOUND_MEDIA) ;;
        *) export -n "$name" ;;
    esac
done
(unset OPENROUTER_API_KEY; install -m 755 "$HERE/p101_solve.sh" "$HOME/p101-solve.sh")
export OPENROUTER_API_KEY
exec "$HOME/hb-venv/bin/python" -u "$HERE/run_ab.py" "$@"
