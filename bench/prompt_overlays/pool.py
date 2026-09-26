"""Build the candidate pool for H4/H5: every django instance of SWE-bench Verified, with its difficulty.

    python bench/prompt_overlays/pool.py            # prints counts, writes results/pool_django.jsonl

Runs in the swebench venv (it needs `datasets`). Spends nothing. The pool is written once, before any
gold validation or model call, and the pre-registration fixes which rows of it are used and in what
order (`slice_order` below), so the order cannot be chosen after an outcome is seen.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "pool_django.jsonl"
# The grading fields (FAIL_TO_PASS, PASS_TO_PASS, test_patch) are left out on purpose: the harness
# reads them from the dataset, and nothing the solver loads should hold the hidden tests.
KEEP = ("instance_id", "repo", "base_commit", "problem_statement", "version", "difficulty")


def slice_order(instance_id: str) -> str:
    """The registered order: sha256 of the id. Deterministic, and blind to outcome and to id order,
    so the pilot's first ten are spread across versions and strata rather than the oldest ten."""
    return hashlib.sha256(instance_id.encode("utf-8")).hexdigest()


def main() -> None:
    from datasets import load_dataset

    rows = [r for r in load_dataset("SWE-bench/SWE-bench_Verified", split="test") if r["repo"] == "django/django"]
    print(f"django instances in Verified: {len(rows)}")
    print("by difficulty:", dict(Counter(r["difficulty"] for r in rows)))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda r: slice_order(r["instance_id"]))
    with OUT.open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps({k: r[k] for k in KEEP}, ensure_ascii=False) + "\n")
    print(f"wrote {OUT} ({len(rows)} rows, in registered sha256 order)")


if __name__ == "__main__":
    main()
