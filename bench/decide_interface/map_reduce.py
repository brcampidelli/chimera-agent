"""The phase-4 demo: one typed question mapped over 1 000 real items, locally, timed.

    python -m bench.decide_interface.map_reduce          # builds the items, runs `chimera decide`, scores

The items are this repository's own last 1 000 commit subjects on `main` with the conventional prefix
(``feat(x):``, ``fix:``…) cut off; the question asks which kind of change the subject describes; the
prefix that was cut is the answer key. The point is the interface — a thousand decisions through
`chimera decide --jsonl`, at US$ 0, with the time it took — and the accuracy is reported as what it is:
a description of one small local model on one easy-to-state task, not a benchmark of anything.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
N = 1000
KINDS = {"feat": "feature", "fix": "fix", "docs": "docs", "test": "test"}
PREFIX = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]*\))?!?:\s*(?P<rest>.+)$")

QUESTIONS = {
    "questions": {
        "kind": {
            "type": "choice",
            "instructions": "You read the subject line of a commit in a software repository. "
                            "What kind of change does the commit make?",
            "criteria": {
                "feature": "adds a capability, a command, a screen or an option that did not exist",
                "fix": "corrects behaviour that was wrong",
                "docs": "changes documentation only",
                "test": "changes tests only",
                "other": "anything else: a benchmark, a refactor, a build or release chore, a dependency bump",
            },
        }
    },
    "decision": "demo.commit_kind",
}


def build_items() -> list[dict[str, str]]:
    log = subprocess.run(
        ["git", "log", "origin/main", "--no-merges", "--format=%h%x09%s", f"-n{N * 2}"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    items: list[dict[str, str]] = []
    for line in log.splitlines():
        sha, _, subject = line.partition("\t")
        m = PREFIX.match(subject.strip())
        if not m:
            continue
        rest = re.sub(r"\s*\(#\d+\)\s*$", "", m.group("rest")).strip()
        items.append({"id": sha, "state": rest, "truth": KINDS.get(m.group("type"), "other")})
        if len(items) == N:
            break
    return items


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    items = build_items()
    (RESULTS / "items.jsonl").write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
    (RESULTS / "questions.json").write_text(json.dumps(QUESTIONS, indent=2), encoding="utf-8")
    out = RESULTS / "answers.jsonl"
    t0 = time.perf_counter()
    subprocess.run(
        [sys.executable, "-m", "chimera.cli.main", "decide", "-q", str(RESULTS / "questions.json"),
         "--jsonl", str(RESULTS / "items.jsonl"), "-o", str(out), "--no-log"],
        cwd=ROOT, check=True,
    )
    seconds = time.perf_counter() - t0
    truth = {i["id"]: i["truth"] for i in items}
    answers = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
    got = {a["id"]: a["answers"]["kind"].get("choice") for a in answers}
    errors = sum(1 for a in answers if "error" in a["answers"]["kind"])
    right = sum(1 for k, v in got.items() if v == truth.get(k))
    confusion = Counter((truth[k], v) for k, v in got.items())
    summary = {
        "items": len(items), "answered": len(answers), "errors": errors, "seconds_total": round(seconds, 1),
        "seconds_per_item": round(seconds / max(len(items), 1), 3), "accuracy": round(right / max(len(got), 1), 4),
        "truth_distribution": Counter(truth.values()), "majority_baseline": round(
            max(Counter(truth.values()).values()) / max(len(items), 1), 4),
        "confusion": {f"{t}->{p}": n for (t, p), n in sorted(confusion.items(), key=lambda x: -x[1])},
    }
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
