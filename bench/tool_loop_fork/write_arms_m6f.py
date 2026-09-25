"""Write the M6-fork harness entries: one per (executor, replica), every one escalating with a snapshot.

Run in WSL:  ~/hb-venv-m6f/bin/python write_arms_m6f.py [config_path]

harness id = `m6f-<strong|weak>-r<k>`. The replica counts are the registered ceilings on runs
(PREREGISTRATION "Design"); the driver stops earlier once an executor has its pairs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

WRAPPER = str(Path.home() / "hb-solve-m6f.sh")
TIMEOUT = 2400
EXECUTORS = {
    "strong": "openrouter/deepseek/deepseek-v3.2",
    "weak": "openrouter/openai/gpt-oss-20b",
}
ESCALATE_TO = {
    "strong": "openrouter/openai/gpt-6-sol",
    "weak": "openrouter/deepseek/deepseek-v3.2",
}
REPLICAS = {"strong": 40, "weak": 120}  # x 4 tasks = 160 / 480 runs at most


def arm_entries() -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for tag, model in EXECUTORS.items():
        for k in range(REPLICAS[tag]):
            hid = f"m6f-{tag}-r{k}"
            entries[hid] = {
                "adapter": "generic_cli",
                "command": WRAPPER,
                "args": ["{task_id}", "{prompt_file}", "{workspace}", hid, model, ESCALATE_TO[tag]],
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
    for key in [k for k in models if k.startswith("m6f-")]:
        del models[key]
    models.update(entries)
    backup = path.with_suffix(path.suffix + ".bak-m6f")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(entries)} entries into {path} (backup {backup.name}); total models now {len(models)}")
    print("sample:", json.dumps(entries["m6f-strong-r0"], ensure_ascii=False))


if __name__ == "__main__":
    main()
