"""Grade the M6-fork pairs with the task's own oracle, the same function on both sides. Read-only on results.

Run in WSL from a LOGIN shell (the oracles run node and pytest), after the runs:
  ~/hb-venv-m6f/bin/python grade_fork.py [--out results/pairs.json]

For every completed cell:
- **copy check** (the apparatus gate): copy the escalated workspace to a scratch path, grade the copy,
  and compare with the score the harness published for that workspace. The stop arm is graded on a
  COPY (the snapshot), so grading a copy at another path must give the harness's number. Any cell off
  by more than 0.001 is reported, and the reader refuses to read pairs while one is.
- **pairs**: for a cell whose breaker tripped, `stop` = the oracle on the snapshot (the workspace at the
  trip — what stopping leaves, since the breaker's ending uses no tools), `esc` = the published score of
  the escalated workspace.
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

HB = Path(os.path.expanduser("~/harness-bench"))
LOGS = Path(os.path.expanduser("~/hb-logs"))
SNAPS = Path(os.path.expanduser("~/hb-snapshots"))
COPIES = Path(os.path.expanduser("~/hb-fork-copies"))
TOL = 0.001
#: The registered tasks. A pilot on another task (011) is never read as a pair.
TASKS = {"041-frontend-state-bug", "042-api-schema-migration", "043-db-migration-safety", "082-compose-config-repair"}


def oracle(task: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"oracle_{task.replace('-', '_')}", HB / "tasks" / task / "oracle_grade.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.score_workspace


def published(hid: str, task: str) -> float | None:
    hits = glob.glob(str(HB / "data_try6" / "results" / hid / "*" / f"{task}.json"))
    if not hits:
        return None
    value = (json.loads(Path(hits[0]).read_text(encoding="utf-8")).get("oracle_result") or {}).get("outcome_score")
    return float(value) if isinstance(value, int | float) else None


def workspace_of(hid: str, task: str) -> Path | None:
    log = LOGS / f"{task}-{hid}.log"
    if not log.is_file():
        return None
    found = re.findall(r"^=== workspace=(.+) ===$", log.read_text(encoding="utf-8", errors="replace"), re.M)
    return Path(found[-1]) if found else None


def score(task: str, workspace: Path) -> float:
    return float(oracle(task)(workspace)["outcome_score"])


def copy_score(task: str, hid: str, workspace: Path) -> float:
    """Grade a copy of `workspace` made the way the agent makes its snapshot (copytree, symlinks kept)."""
    dest = (COPIES / f"{task}-{hid}").resolve()
    if dest.parent != COPIES.resolve():
        raise SystemExit(f"refusing to write {dest}")
    shutil.rmtree(dest, ignore_errors=True)
    COPIES.mkdir(parents=True, exist_ok=True)
    shutil.copytree(workspace, dest, symlinks=True)
    try:
        return score(task, dest)
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("results/pairs.json"))
    args = ap.parse_args()
    rows: list[dict[str, Any]] = []
    for result in sorted(glob.glob(str(HB / "data_try6" / "results" / "m6f-*" / "*" / "*.json"))):
        hid, task = Path(result).parts[-3], Path(result).stem
        if task not in TASKS:
            continue
        pub, ws = published(hid, task), workspace_of(hid, task)
        snap = SNAPS / f"{task}-{hid}"
        row: dict[str, Any] = {"task": task, "hid": hid, "executor": hid.split("-")[1], "esc": pub,
                               "tripped": snap.is_dir(), "stop": None, "copy_score": None}
        if ws is not None and ws.is_dir() and pub is not None:
            row["copy_score"] = copy_score(task, hid, ws)
        if row["tripped"]:
            row["stop"] = score(task, snap)
        row["copy_ok"] = row["copy_score"] is not None and abs(row["copy_score"] - (pub or 0.0)) <= TOL
        rows.append(row)
        print(f"{task:<26} {hid:<16} esc={pub} copy={row['copy_score']} ok={row['copy_ok']} "
              f"trip={int(row['tripped'])} stop={row['stop']}", flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    bad = [r for r in rows if not r["copy_ok"]]
    print(f"\ncells {len(rows)} · tripped {sum(r['tripped'] for r in rows)} · copy check failed {len(bad)}")


if __name__ == "__main__":
    main()
