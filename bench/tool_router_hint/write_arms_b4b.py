"""Write the B4b arm entries (study 22 phase 6; the treated arm adds `--tool-router-mode hint`) (2 arms x 3 executors x 3 replicas = 18) into the harness config.

Run in WSL:  ~/hb-venv-b4/bin/python write_arms_b4.py [config_path]

harness id = `sys1-<on|off>-<exec>-r<k>`, one per replica because the harness overwrites
`results/<harness>/<task>.json` per (harness, task) and `CHIMERA_HOME` is per (task, harness).
The command is `~/hb-solve-b4.sh`, which carries the fixed flags; only the executor model and the
`--tool-router` flag vary here, so the two arms of a pair differ by the router and nothing else.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

WRAPPER = str(Path.home() / "hb-solve-b4b.sh")
TIMEOUT = 2400
ROUTER_MODEL = "openrouter/deepseek/deepseek-v4-flash-0731"
EXECUTORS = {
    # The factorial's model: #453's noise floor (SD 0.073 on the same 23 tasks) is about THIS
    # executor, so the effect here is read against a floor that was measured, not assumed.
    "strong": "openrouter/deepseek/deepseek-v3.2",
    # The other half of the registered prediction: "with a weak executor, a gain". Amendment 1:
    # the registered mistral-small is 429 upstream on the shared pool and cannot run.
    "weak": "openrouter/openai/gpt-oss-20b",
    # Amendment 6 (approved at US$ 38): what the router does to a model that is actually good.
    # Released the day of this run; read as its own paired contrast, never pooled with the others.
    "sol": "openrouter/openai/gpt-6-sol",
}


def arm_entries() -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for tag, model in EXECUTORS.items():
        for on in (0, 1):
            for k in (0, 1, 2):
                hid = f"sys1h-{'on' if on else 'off'}-{tag}-r{k}"
                flags = ["--tool-router", ROUTER_MODEL, "--tool-router-mode", "hint"] if on else []
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
    for key in [k for k in models if k.startswith("sys1h-")]:
        del models[key]
    models.update(entries)
    backup = path.with_suffix(path.suffix + ".bak-b4b")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(entries)} arm entries into {path} (backup {backup.name}); total models now {len(models)}")
    print("sample:", json.dumps(entries["sys1h-on-strong-r0"], ensure_ascii=False))


if __name__ == "__main__":
    main()
