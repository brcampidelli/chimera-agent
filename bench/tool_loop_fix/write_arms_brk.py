"""Write the breaker re-measurement's harness entries: 2 arms x 2 executors x 10 replicas = 40.

Run in WSL:  ~/hb-venv-brk-fixed/bin/python write_arms_brk.py [config_path]

harness id = `brk-<legacy|fixed>-<strong|weak>-r<k>`. The arm picks the venv (the frozen tree it imports);
nothing else differs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

WRAPPER = str(Path.home() / "hb-solve-brk.sh")
TIMEOUT = 2400
EXECUTORS = {
    "strong": "openrouter/deepseek/deepseek-v3.2",
    "weak": "openrouter/openai/gpt-oss-20b",
}
VENVS = {"legacy": "hb-venv-brk-legacy", "fixed": "hb-venv-brk-fixed"}
REPLICAS = 10


def arm_entries() -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for tag, model in EXECUTORS.items():
        for arm, venv in VENVS.items():
            for k in range(REPLICAS):
                hid = f"brk-{arm}-{tag}-r{k}"
                entries[hid] = {
                    "adapter": "generic_cli",
                    "command": WRAPPER,
                    "args": ["{task_id}", "{prompt_file}", "{workspace}", hid, model, venv],
                    "timeout_sec": TIMEOUT,
                    "session_prefix": f"hb-{hid}",
                }
    return entries


def load_cfg(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        stripped = re.sub(r",(\s*[}\]])", r"\1", text)
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            import yaml

            return yaml.safe_load(text) or {}


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.path.expanduser("~/harness-bench/config/harness.yaml"))
    cfg = load_cfg(path)
    models = cfg.setdefault("models", {})
    entries = arm_entries()
    for key in [k for k in models if k.startswith("brk-")]:
        del models[key]
    models.update(entries)
    backup = path.with_suffix(path.suffix + ".bak-brk")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(entries)} entries into {path} (backup {backup.name}); total models now {len(models)}")
    print("sample:", json.dumps(entries["brk-fixed-strong-r0"], ensure_ascii=False))


if __name__ == "__main__":
    main()
