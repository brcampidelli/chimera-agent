"""The local-disk half of the wall: which solves reached outside their own workspace.

    python bench/default_model/disk_scan.py [main_solves|pilot_solves|discarded_main_before_amendment3]

The network wall (H4/H5 Amendment 2) and the git wall hold, but `run_shell` runs on the host with
no filesystem sandbox, and this host holds the answers several times over: the django reference
clones at `main` (every later fix), the templates and workspaces of other items (a later base is a
later django), the grading logs (`patch.diff` is the gold patch in a gold run; `eval.sh` carries
the hidden tests) and the Hugging Face cache of SWE-bench itself. Found on 2026-09-26 when a solve
ran `find` across the grading directories while looking for a dependency.

This reads every recorded shell command, keeps each absolute path that is not the solve's own
workspace or own scratch, and classifies it. A path that could hold a later django or a grader's
answer is a SOURCE touch; the item is then listed, and the report reads every comparison again
without those items. A command's text is recorded to 500 characters, so a touch inside a longer
command can be missed: this is a lower bound.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
# A colon ends a path: `PYTHONPATH=/own/ws:/usr/lib/...` names two paths, not one.
_PATH = re.compile(r"/home/[A-Za-z0-9_.-]+(?:/[^\s'\";|&<>)(`:]*)?")
#: Paths that can hold a django other than the item's own base commit, or a grader's answer.
_SOURCE = re.compile(
    r"(django-ref|/templates/|__django__django-\d+|-grade(?:/|$)|run_evaluation|huggingface"
    r"|patch\.diff|eval\.sh|h45-cache|dflt-cache)")
#: Searches that walk the whole machine: they can print a SOURCE path without naming one.
_BROAD = re.compile(r"(\bfind\s+/(?:\s|$)|\bfind\s+/home\b|\bfind\s+~|grep\s+-[a-zA-Z]*r[a-zA-Z]*\s[^|;&]*\s/home\b"
                    r"|\blocate\s|\bdocker\s)")


def classify(row: dict[str, Any]) -> dict[str, Any]:
    own_ws = f"{row['arm']}__{row['instance_id']}"
    source: list[str] = []
    other: list[str] = []
    broad: list[str] = []
    for cmd in row.get("shell") or []:
        if _BROAD.search(cmd):
            broad.append(cmd[:200])
        for path in _PATH.findall(cmd):
            parts = path.split("/")
            if len(parts) > 3 and parts[3].endswith("h45-work") and len(parts) > 4:
                name = parts[4]
                if name == own_ws or name.startswith(f"dflt-{row['arm']}-") or name.startswith(f"h45-{row['arm']}-"):
                    # Own workspace or own scratch: except a reference from inside it to another item.
                    rest = "/".join(parts[5:])
                    if not _SOURCE.search(rest):
                        continue
            if _SOURCE.search(path):
                source.append(path)
            else:
                other.append(path)
    return {"source": sorted(set(source)), "other": sorted(set(other)), "broad": broad}


def scan(name: str) -> dict[str, Any]:
    """``name`` is a results file without `.jsonl`: `main_solves`, `pilot_solves`, ..."""
    rows = [json.loads(ln) for ln in (RESULTS / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
            if ln.strip()]
    per_arm: dict[str, Counter[str]] = {}
    items: dict[str, list[str]] = {}
    detail = []
    for r in rows:
        c = classify(r)
        t = per_arm.setdefault(r["arm"], Counter())
        t["solves"] += 1
        t["source_touch"] += bool(c["source"])
        t["broad_search"] += bool(c["broad"])
        t["other_path"] += bool(c["other"])
        if c["source"]:
            items.setdefault(r["instance_id"], []).append(r["arm"])
            detail.append({"instance_id": r["instance_id"], "arm": r["arm"], "source": c["source"][:12]})
    return {"per_arm": {a: dict(v) for a, v in sorted(per_arm.items())},
            "source_items": dict(sorted(items.items())), "detail": detail}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    print(json.dumps(scan(sys.argv[1] if len(sys.argv) > 1 else "main_solves"), indent=2))
