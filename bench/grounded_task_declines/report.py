"""The registered readings of `bench/grounded_task_declines` (PREREGISTRATION.md §4). US$ 0.

    python bench/grounded_task_declines/report.py <out-dir>

Each item's route through the check is one of:

- ``gate_declined``: the verifier read the draft as not supported, escalated, and shipped a decline.
  The draft was withheld. **This is the harm the bench exists to count.**
- ``drafter_declined``: the verifier read the draft itself as a decline and shipped it.
- ``escalated``: Sol's redraft, written under the question prompt, was shipped instead of the draft.
- ``supported``: the draft shipped, verified.
- ``unverified`` / ``lexical``: the draft shipped without a verifier reading.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from chimera.eval import proportions

ROUTES = ("gate_declined", "drafter_declined", "escalated", "supported", "unverified", "lexical")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    return proportions.wilson(k, n, z)


def route(row: dict) -> str:
    g = row["grounded"]
    if g["decline_shipped"]:
        return "gate_declined" if g["escalated"] else "drafter_declined"
    return str(g["outcome"])


def verdict(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    if hi <= 0.10:
        return "HARMLESS (upper bound <= 10%)"
    if k / n >= 0.25:
        return "MATTERS (point >= 25%)"
    return "MODERATE (neither)"


def line(label: str, k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{label:44} {k:3d} / {n:3d}  {k / n if n else 0:6.1%}  [{lo:5.1%}, {hi:5.1%}]"


def main() -> int:
    out = Path(sys.argv[1])
    rows = [json.loads(x) for x in (out / "runs.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    for r in rows:
        r["route"] = route(r)
    tasks = [r for r in rows if r["family"] == "T"]
    ctrl = [r for r in rows if r["family"] == "C"]

    c_decl = sum(1 for r in ctrl if r["route"] in ("gate_declined", "drafter_declined"))
    print("== control (answerable questions; known: 0/141 declined in bench/verified_cascade arm D)")
    print(line("declined", c_decl, len(ctrl)))
    control_ok = c_decl <= 2
    print("control gate:", "PASS" if control_ok else "FAIL — apparatus suspect, the task readings are not read")
    print("routes:", dict(Counter(r["route"] for r in ctrl)))

    print("\n== tasks forced through the check")
    print("routes:", {k: sum(1 for r in tasks if r["route"] == k) for k in ROUTES})
    k = sum(1 for r in tasks if r["route"] == "gate_declined")
    print(line("PRIMARY gate_declined", k, len(tasks)))
    if control_ok and tasks:
        print("verdict:", verdict(k, len(tasks)))
    for lang in ("pt", "en"):
        part = [r for r in tasks if r["lang"] == lang]
        print(line(f"  {lang}", sum(1 for r in part if r["route"] == "gate_declined"), len(part)))
    for kind in sorted({r["kind"] for r in tasks}):
        part = [r for r in tasks if r["kind"] == kind]
        print(line(f"  {kind}", sum(1 for r in part if r["route"] == "gate_declined"), len(part)))
    any_decl = sum(1 for r in tasks if r["route"] in ("gate_declined", "drafter_declined"))
    print(line("any decline shipped (gate + drafter)", any_decl, len(tasks)))
    print(line("escalated to Sol (cost)", sum(1 for r in tasks if r["grounded"]["escalated"]), len(tasks)))

    real = [r for r in tasks if r["classifier_reads_question"]]
    print("\n== the product's path: tasks the classifier reads as questions (descriptive, small n)")
    print(line("gate_declined", sum(1 for r in real if r["route"] == "gate_declined"), len(real)))

    usd_t = sum(r["usd_total"] for r in tasks)
    usd_c = sum(r["usd_total"] for r in ctrl)
    print(f"\nspend: tasks US$ {usd_t:.4f}, control US$ {usd_c:.4f}, total US$ {usd_t + usd_c:.4f}")

    print("\n== every task whose shipped text is not its draft, verbatim (for the manual reading, §4.3)")
    for r in tasks:
        if r["shipped"] != r["draft"]:
            print(f"\n--- {r['item_id']} [{r['route']}] p={r['grounded']['p']} :: {r['message']}")
            print("DRAFT:", r["draft"][:700].replace("\n", " "))
            print("SHIPPED:", r["shipped"][:400].replace("\n", " "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
