"""The reading of `PREREGISTRATION.md`: how often the three verifier-integrity flags fire on work
whose outcome a DIFFERENT process graded. Deterministic, stdlib, US$ 0.

    python bench/verifier_integrity/measure.py            # both corpora, writes results/readings.json

Two corpora, both already on disk:

* **Harness-Bench factorial** (`~/hb-homes`, `~/harness-bench`, the 547 solves of
  `bench/false_success`): every attempt's stored ``diffs`` (path + unified patch, clipped by the
  receipt writer) read through :func:`flag_patches`, split by the oracle's grade. Nothing here is
  redistributable, so the repo keeps counts only.
* **The labelled fix history** (`bench/test_gate_two_sided/results/patches-all.jsonl`): the buggy
  base from `bench/local_lift/tasks.py`, the post-patch files from the record, read through
  :func:`flag_snapshots` with the command that graded it, split by the hidden test's label.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bench" / "local_lift"))

from chimera.eval import proportions  # noqa: E402
from chimera.governance.verifier_integrity import (  # noqa: E402  (path set immediately above)
    KINDS,
    flag_patches,
    flag_snapshots,
)

PASS_THRESHOLD = 0.8  # the same cut `bench/false_success/corpus.py` and `read_results.py` use
_HID = re.compile(r"^(?P<task>.*)-(?P<hid>arm-\d{3}-r\d)$")


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    # One home for the arithmetic (chimera/eval/proportions.py), with the z this bench registered.
    return [round(bound, 4) for bound in proportions.wilson(k, n, z)]


def _rate(k: int, n: int) -> dict[str, Any]:
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "wilson95": wilson(k, n)}


def _oracle_of(bench_home: Path, hid: str, task: str) -> float | None:
    hits = glob.glob(str(bench_home / "data_try6" / "results" / hid / "*" / f"{task}.json"))
    if not hits:
        return None
    payload = json.loads(Path(hits[0]).read_text(encoding="utf-8"))
    value = (payload.get("oracle_result") or {}).get("outcome_score")
    return float(value) if isinstance(value, (int, float)) else None


def harness_bench() -> dict[str, Any]:
    homes = Path(os.path.expanduser("~/hb-homes"))
    bench_home = Path(os.path.expanduser("~/harness-bench"))
    rows: list[dict[str, Any]] = []
    dropped: Counter[str] = Counter()
    for directory in sorted(glob.glob(str(homes / "*"))):
        match = _HID.match(os.path.basename(directory))
        if not match:
            dropped["not a factorial arm"] += 1
            continue
        receipt = Path(directory) / "runs.jsonl"
        if not receipt.is_file():
            dropped["no receipt"] += 1
            continue
        lines = [json.loads(r) for r in receipt.read_text(encoding="utf-8").splitlines() if r.strip()]
        if not lines:
            dropped["empty receipt"] += 1
            continue
        oracle = _oracle_of(bench_home, match.group("hid"), match.group("task"))
        if oracle is None:
            dropped["no oracle score"] += 1
            continue
        last = lines[-1]
        diffs = [d for a in (last.get("attempts") or []) for d in (a.get("diffs") or [])]
        flags = flag_patches(
            [(str(d.get("path") or ""), str(d.get("patch") or "")) for d in diffs],
            verify_command=str(last.get("verify_command") or ""),
        )
        rows.append({
            "task": match.group("task"),
            "passed": oracle >= PASS_THRESHOLD,
            "self_report": bool(last.get("success")),
            "kinds": sorted({f.kind for f in flags}),
            "files": len(diffs),
            "truncated": sum(1 for d in diffs if d.get("truncated")),
            "has_verify_command": bool(last.get("verify_command")),
            # Paths only, never patch text: the task material carries no licence.
            "flagged_paths": sorted({f"{f.kind}:{os.path.basename(f.path)}" for f in flags}),
        })
    return _summarise(rows, "passed", dict(dropped))


def fix_history() -> dict[str, Any]:
    from tasks import TASKS  # bench/local_lift

    by_id = {str(t["id"]): t for t in TASKS}
    rows: list[dict[str, Any]] = []
    path = REPO / "bench" / "test_gate_two_sided" / "results" / "patches-all.jsonl"
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        rec = json.loads(raw)
        if rec.get("unpatched") or rec.get("label") not in ("correct", "incorrect"):
            continue
        task = by_id[str(rec["task_id"])]
        before = {str(k): str(v) for k, v in task["files"].items()}
        after = dict(before)
        after.update({str(k): str(v) for k, v in (rec.get("files") or {}).items()})
        # The command `run_patches.grade` runs: the hidden test, by file. (`task["verify"]` is the
        # file under repair, not a command.)
        command = f"python -m pytest -q -p no:cacheprovider {task['test']}"
        flags = flag_snapshots(before, after, verify_command=command)
        rows.append({
            "task": rec["task_id"],
            "passed": rec["label"] == "correct",
            "self_report": True,
            "kinds": sorted({f.kind for f in flags}),
            "files": len(rec.get("files") or {}),
            "truncated": 0,
            "has_verify_command": True,
            "flagged_paths": sorted({f"{f.kind}:{f.path}" for f in flags}),
        })
    return _summarise(rows, "passed", {})


def _summarise(rows: list[dict[str, Any]], label: str, dropped: dict[str, int]) -> dict[str, Any]:
    good = [r for r in rows if r[label]]
    bad = [r for r in rows if not r[label]]
    out: dict[str, Any] = {
        "solves": len(rows),
        "dropped": dropped,
        "oracle_pass": len(good),
        "oracle_fail": len(bad),
        "with_verify_command": sum(1 for r in rows if r["has_verify_command"]),
        "solves_with_a_truncated_patch": sum(1 for r in rows if r["truncated"]),
        "any_flag": {
            "on_pass (false-positive rate)": _rate(sum(1 for r in good if r["kinds"]), len(good)),
            "on_fail": _rate(sum(1 for r in bad if r["kinds"]), len(bad)),
        },
    }
    for kind in KINDS:
        out[kind] = {
            "on_pass (false-positive rate)": _rate(sum(1 for r in good if kind in r["kinds"]), len(good)),
            "on_fail": _rate(sum(1 for r in bad if kind in r["kinds"]), len(bad)),
        }
    out["flagged_paths"] = dict(Counter(p for r in rows for p in r["flagged_paths"]).most_common())
    out["flagged_tasks"] = dict(Counter(r["task"] for r in rows if r["kinds"]).most_common())
    return out


def main() -> None:
    readings = {"harness_bench": harness_bench(), "fix_history": fix_history()}
    target = HERE / "results" / "readings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(readings, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(readings, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
