"""Write the M6 arm entries (study 24; the treated arm adds `--escalate-on-tool-loop TARGET`)
(2 arms x 2 executors x 3 replicas = 12) into the harness config.

Run in WSL:  ~/hb-venv-m6/bin/python write_arms_m6.py [config_path]

harness id = `m6-<stop|esc>-<exec>-r<k>`, one per replica, because the harness overwrites
`results/<harness>/<task>.json` per (harness, task) and `CHIMERA_HOME` is per (task, harness).
Derived from B4b's writer: only the ids, the executors and the one flag differ.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

WRAPPER = str(Path.home() / "hb-solve-m6.sh")
TIMEOUT = 2400
EXECUTORS = {
    "strong": "openrouter/deepseek/deepseek-v3.2",
    "weak": "openrouter/openai/gpt-oss-20b",
}
#: One tier up, as LiteLLM's stall_escalation does: weak -> strong, strong -> Sol.
ESCALATE_TO = {
    "strong": "openrouter/openai/gpt-6-sol",
    "weak": "openrouter/deepseek/deepseek-v3.2",
}


def arm_entries() -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for tag, model in EXECUTORS.items():
        for esc in (0, 1):
            for k in (0, 1, 2):
                hid = f"m6-{'esc' if esc else 'stop'}-{tag}-r{k}"
                flags = ["--escalate-on-tool-loop", ESCALATE_TO[tag]] if esc else []
                entries[hid] = {
                    "adapter": "generic_cli",
                    "command": WRAPPER,
                    "args": ["{task_id}", "{prompt_file}", "{workspace}", hid, model, *flags],
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
    for key in [k for k in models if k.startswith("m6-")]:
        del models[key]
    models.update(entries)
    backup = path.with_suffix(path.suffix + ".bak-m6")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(entries)} arm entries into {path} (backup {backup.name}); total models now {len(models)}")
    print("sample:", json.dumps(entries["m6-esc-strong-r0"], ensure_ascii=False))


if __name__ == "__main__":
    main()
