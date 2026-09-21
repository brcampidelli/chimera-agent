"""Launch a bench run with the repository's `.env` loaded into the environment.

The bench runners read `OPENROUTER_API_KEY` from `os.environ` directly (they are standalone scripts,
not the package), so the key that lives in `.env` — where the desktop keeps it — has to be put there
before the runner starts. This does exactly that and nothing else: it never prints the value, and it
never writes it anywhere.

    python bench/_with_env.py bench/jev_decisions/run.py --run --tier-b --arms Jr,Js,Jb,Jbatch --out ...
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> int:
    if not path.is_file():
        return 0
    loaded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        # The environment wins, always — the same rule `chimera.config_vault` states. A key already
        # exported for this shell is the one the person meant.
        if name and value and name not in os.environ:
            os.environ[name] = value
            loaded += 1
    return loaded


if __name__ == "__main__":
    count = load_env(REPO / ".env")
    print(f"loaded {count} variable(s) from .env", file=sys.stderr)
    target = sys.argv[1]
    sys.argv = sys.argv[1:]
    runpy.run_path(target, run_name="__main__")