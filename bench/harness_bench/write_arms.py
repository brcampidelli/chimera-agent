"""Write the 24 `generic_cli` arm entries (8 arms x 3 replicas) into the harness config.

Run in WSL against the ext4 clone:  ~/hb-venv/bin/python write_arms.py [config_path]

Each arm is one combination of the three factors read from the code (`PREREGISTRATION.md` §3):
  A = repo-map  (--repo-map)
  B = checklist (--checklist)
  C = planner   (on = no flag; off = --no-plan)
harness id = `arm-<abc>-r<k>` with a,b,c in {0,1} — 24 keys, because the harness overwrites
`results/<harness>/<task>.json` per (harness, task), so each replica needs its own harness id.

The command is the wrapper `~/hb-solve.sh`, which carries the fixed flags and the model; only the
per-arm factor flags vary here. Config is written as strict JSON (the harness loader tries json.loads
first), after backing up the original.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

WRAPPER = str(Path.home() / "hb-solve.sh")
TIMEOUT = 2400


def factor_flags(a: int, b: int, c: int) -> list[str]:
    flags: list[str] = []
    if a:
        flags.append("--repo-map")
    if b:
        flags.append("--checklist")
    if not c:  # planner OFF
        flags.append("--no-plan")
    return flags


def arm_entries() -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for a in (0, 1):
        for b in (0, 1):
            for c in (0, 1):
                for k in (0, 1, 2):
                    hid = f"arm-{a}{b}{c}-r{k}"
                    entries[hid] = {
                        "adapter": "generic_cli",
                        "command": WRAPPER,
                        "args": ["{task_id}", "{prompt_file}", "{workspace}", hid, *factor_flags(a, b, c)],
                        "timeout_sec": TIMEOUT,
                        "session_prefix": f"hb-{hid}",
                    }
    return entries


def load_cfg(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        stripped = re.sub(r",(\s*[}\]])", r"\1", text)  # tolerate trailing commas
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
    # Remove any prior arm-* entries so a re-run is idempotent, keep every other model untouched.
    for key in [k for k in models if k.startswith("arm-")]:
        del models[key]
    models.update(entries)

    backup = path.with_suffix(path.suffix + ".bak-hbench")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(entries)} arm entries into {path} (backup {backup.name}); total models now {len(models)}")
    print("sample:", json.dumps(entries["arm-101-r0"], ensure_ascii=False))


if __name__ == "__main__":
    main()
