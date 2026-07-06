"""Collect per-task pass/fail from two Terminal-Bench run outputs into the A/B trial lists.

Terminal-Bench's exact results schema varies by version, so this is defensive: it walks each
output dir for JSON, and for every task-like record it reads a resolved/passed/success boolean under
any of the common field names. Both arms are projected onto the SAME sorted task-id set (a task
missing from an arm counts as failed) so `chimera bench-compare` scores identical tasks.

Usage: python collect.py <baseline_dir> <chimera_dir> <out_dir>
Writes <out_dir>/baseline.json and <out_dir>/chimera.json (JSON lists of booleans, task-id order).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PASS_KEYS = ("resolved", "passed", "is_resolved", "success", "solved", "correct")
_ID_KEYS = ("task_id", "id", "instance_id", "task", "name")


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "pass", "passed", "resolved", "1", "yes")
    return False


def _harvest(obj: object, out: dict[str, bool]) -> None:
    """Recursively pull {task_id: passed} from any nested record that looks like a task result."""
    if isinstance(obj, dict):
        tid = next((str(obj[k]) for k in _ID_KEYS if k in obj), None)
        pk = next((k for k in _PASS_KEYS if k in obj), None)
        if tid is not None and pk is not None:
            out[tid] = _truthy(obj[pk])
        for value in obj.values():
            _harvest(value, out)
    elif isinstance(obj, list):
        for item in obj:
            _harvest(item, out)


def _results_for(run_dir: Path) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for jf in run_dir.rglob("*.json"):
        try:
            _harvest(json.loads(jf.read_text(encoding="utf-8")), out)
        except (json.JSONDecodeError, OSError):
            continue
    return out


def main(baseline_dir: str, chimera_dir: str, out_dir: str) -> None:
    base = _results_for(Path(baseline_dir))
    treat = _results_for(Path(chimera_dir))
    task_ids = sorted(set(base) | set(treat))
    if not task_ids:
        print("WARNING: no task results found — check the TB output dirs / result schema.")
    out = Path(out_dir)
    (out / "baseline.json").write_text(json.dumps([base.get(t, False) for t in task_ids]), encoding="utf-8")
    (out / "chimera.json").write_text(json.dumps([treat.get(t, False) for t in task_ids]), encoding="utf-8")
    print(f"tasks: {len(task_ids)} | baseline {sum(base.values())} pass | chimera {sum(treat.values())} pass")
    print(f"wrote {out/'baseline.json'} and {out/'chimera.json'}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
