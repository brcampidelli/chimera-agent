"""The Manager-as-gate census — US$ 0, reads only files tracked in this repository.

    python bench/manager_advisory/census.py   # writes results/census.json

Five readings, fixed in PREREGISTRATION.md before this file was run:
C1 attempt `evidence` labels in stored rows; C2 the configured regime of every bench with per-solve
rows; C3 tool_defer's run verdict x oracle; C4 whether a reverted attempt is recoverable; C5 the
manager_diff corpus, control first.

The regime of each dataset is read from its runner (cited per row), because the artifacts do not
record their own configuration — the limit PREREGISTRATION.md names under §2m.
"""

from __future__ import annotations

import json
import random
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BENCH = REPO / "bench"
EVIDENCE_VALUES = {"verifier", "diff+manager", "manager", "diff", "none"}
PASS = 0.8  # manager_p / manager_diff: oracle >= 0.8 is a true success


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _tracked(pattern: str) -> list[Path]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", pattern], capture_output=True,
                         text=True, check=True).stdout
    return [REPO / p for p in out.splitlines() if p.strip()]


# --- C1 ---------------------------------------------------------------------------------------------

def _walk_evidence(node: Any, hits: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "evidence" and isinstance(value, str) and value in EVIDENCE_VALUES:
                hits.append(value)
            else:
                _walk_evidence(value, hits)
    elif isinstance(node, list):
        for item in node:
            _walk_evidence(item, hits)


def c1_receipts() -> dict[str, Any]:
    files = [p for p in _tracked("bench") if p.suffix in (".json", ".jsonl")]
    by_file: dict[str, dict[str, int]] = {}
    unreadable = 0
    for path in files:
        hits: list[str] = []
        try:
            docs = _jsonl(path) if path.suffix == ".jsonl" else [json.loads(path.read_text(encoding="utf-8"))]
        except (json.JSONDecodeError, UnicodeDecodeError):
            unreadable += 1
            continue
        for doc in docs:
            _walk_evidence(doc, hits)
        if hits:
            counts: dict[str, int] = {}
            for h in hits:
                counts[h] = counts.get(h, 0) + 1
            by_file[str(path.relative_to(REPO))] = counts
    return {"files_scanned": len(files), "unreadable": unreadable, "files_with_labels": by_file,
            "labels_total": sum(sum(c.values()) for c in by_file.values())}


# --- C2 ---------------------------------------------------------------------------------------------

def _swe_rows() -> list[dict[str, Any]]:
    """One row per (run family, arm, instance); `run1_rem` / `run2_rem` are merged into their parent."""
    seen: dict[tuple[str, str, str], None] = {}
    for path in sorted((BENCH / "swe_bench" / "results").glob("*/predictions_*.jsonl")):
        family = path.parent.name.removesuffix("_rem")
        arm = path.stem.removeprefix("predictions_")
        for row in _jsonl(path):
            seen[(family, arm, row["instance_id"])] = None
    return [{"family": f, "arm": a, "id": i} for f, a, i in seen]


def _count_learning(path: Path) -> int:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("per_seed"):
        return sum(len(s.get("cold", [])) + len(s.get("learning", [])) for s in doc["per_seed"])
    return sum(len(v.get("passed", [])) for v in doc.get("by_arm", {}).values())


def c2_regimes() -> dict[str, Any]:
    swe = _swe_rows()
    swe_base = sum(1 for r in swe if r["arm"] == "baseline")
    unattended = sum(len(r["runs"]) for p in _tracked("bench/unattended_claims/results/*.jsonl") for r in _jsonl(p))
    local = sum(2 * len(_jsonl(p)) for p in _tracked("bench/local_lift/_reverify_*/journal.jsonl"))
    learning = sum(_count_learning(p) for p in _tracked("bench/learning_lift/results*/learning.json"))
    learning += len(json.loads((BENCH / "learning_lift/results_probe/probe.json").read_text())["rows"])
    test_gate = {(r["task_id"], r["replica"], r["model"])
                 for p in _tracked("bench/test_gate_two_sided/results/patches*.jsonl") for r in _jsonl(p)}
    edit = sum(len(json.loads(p.read_text(encoding="utf-8"))["rows"])
               for p in _tracked("bench/edit_tools/results/*.json"))
    # Each row carries how it was configured and the runner line that says so.
    datasets: list[dict[str, Any]] = [
        {"bench": "harness_bench factorial", "solves": len(_jsonl(BENCH / "harness_bench/results/2026-09-13-factorial.jsonl")),
         "manager": False, "verifier": False, "source": "harness_bench/PREREGISTRATION.md:36 --no-manager"},
        {"bench": "swe_bench baseline arms", "solves": swe_base, "manager": False, "verifier": False,
         "source": "swe_bench/run_swe.py _ARMS['baseline'] --no-manager"},
        {"bench": "swe_bench treatment arms", "solves": len(swe) - swe_base, "manager": True, "verifier": False,
         "source": "swe_bench/run_swe.py _SCAFFOLD (no --no-manager, no --verify, --keep-workspace)",
         "run_verdict_stored": False, "manager_saw_diff": False},
        {"bench": "tool_defer", "solves": len(_jsonl(BENCH / "tool_defer/results.jsonl")), "manager": True,
         "verifier": False, "source": "tool_defer/run_paired.py _solve (no --no-manager, no --verify)",
         "run_verdict_stored": True, "manager_saw_diff": True},
        {"bench": "test_gate_two_sided", "solves": len(test_gate), "manager": False, "verifier": False,
         "source": "test_gate_two_sided/run_patches.py:35 --no-manager",
         "note": "patches-all.jsonl is the union of the other two files; deduplicated by (task, replica, model)"},
        {"bench": "unattended_claims", "solves": unattended, "manager": False, "verifier": False,
         "source": "unattended_claims/run.py:231 use_manager=False"},
        {"bench": "local_lift journals", "solves": local, "manager": "half", "verifier": True,
         "source": "local_lift/run_paired.py --verify on both arms"},
        {"bench": "learning_lift", "solves": learning, "manager": True, "verifier": True,
         "source": "learning_lift/run_learning.py:154 --verify"},
        {"bench": "retry_lift", "solves": len(json.loads((BENCH / "retry_lift/results/retry.json").read_text())["rows"]),
         "manager": True, "verifier": True, "source": "retry_lift/run_retry.py:82 --verify"},
        {"bench": "edit_tools", "solves": edit, "manager": True, "verifier": True,
         "source": "edit_tools/run_pilot.py:80 --verify"},
    ]
    total = sum(d["solves"] for d in datasets)
    deciding = sum(d["solves"] for d in datasets if d["manager"] is True and not d["verifier"])
    usable = sum(d["solves"] for d in datasets
                 if d["manager"] is True and not d["verifier"] and d.get("run_verdict_stored"))
    return {"datasets": datasets, "solves_total": total, "manager_decides": deciding,
            "manager_decides_share": round(deciding / total, 3),
            "manager_decides_with_verdict_and_oracle": usable}


# --- C3 / C4 ----------------------------------------------------------------------------------------

#: The value each tool_defer task's shell verifier checks, for the descriptive half of C4 only.
_EXPECTED = {"csv_total": ["45.50"], "primes_sum": ["24133"], "doc_field": ["14"],
             "stats_median": ["5"]}


def c3_c4_tool_defer() -> dict[str, Any]:
    rows = _jsonl(BENCH / "tool_defer/results.jsonl")
    halted = [r for r in rows if r["timed_out"]]
    live = [r for r in rows if not r["timed_out"]]
    table = {"approved_pass": 0, "approved_fail": 0, "failed_pass": 0, "failed_fail": 0}
    claims: list[dict[str, Any]] = []
    for r in live:
        verdict = "approved" if r["exit"] == 0 else "failed"
        table[f"{verdict}_{'pass' if r['completed'] else 'fail'}"] += 1
        if verdict == "failed":
            values = _EXPECTED.get(r["task"], [])
            claims.append({"task": r["task"], "arm": r["arm"], "repeat": r["repeat"],
                           "answer_mentions_expected_value": any(re.search(rf"(?<![\d.]){re.escape(v)}(?![\d])", r["tail"]) for v in values) if values else None})
    approved = table["approved_pass"] + table["approved_fail"]
    failed = table["failed_pass"] + table["failed_fail"]
    keys_per_row = sorted({k for r in rows for k in r})
    return {
        "c3": {"runs": len(rows), "halted": len(halted), **table,
               "approved_precision": round(table["approved_pass"] / approved, 3) if approved else None,
               "share_all_attempts_reverted": round(failed / len(live), 3) if live else None},
        "c4": {"fields_stored_per_row": keys_per_row,
               "reverted_work_recoverable": any(k in keys_per_row for k in ("diffs", "discarded_at", "attempts")),
               # A regex can only say the value is MENTIONED; whether the answer claims to have
               # written it, or says it could not, is read by eye in RESULTS.md (§2e).
               "failed_runs_whose_answer_mentions_the_expected_value": sum(1 for c in claims if c["answer_mentions_expected_value"]),
               "failed_runs_on_value_tasks": sum(1 for c in claims if c["answer_mentions_expected_value"] is not None),
               "failed_runs": len(claims)},
    }


# --- C5 ---------------------------------------------------------------------------------------------

def _cluster_ci(rows: list[dict[str, Any]], stat: Callable[[list[dict[str, Any]]], float],
                n: int = 2000, seed: int = 7) -> list[float]:
    tasks = sorted({r["task_id"] for r in rows})
    by_task = {t: [r for r in rows if r["task_id"] == t] for t in tasks}
    rng = random.Random(seed)
    draws = sorted(stat([r for t in rng.choices(tasks, k=len(tasks)) for r in by_task[t]]) for _ in range(n))
    return [round(draws[int(0.025 * n)], 3), round(draws[int(0.975 * n) - 1], 3)]


def _p_false_given_revise_minus_base(rows: list[dict[str, Any]]) -> float:
    revise = [r for r in rows if not r["approved"]]
    if not revise or not rows:
        return 0.0
    return sum(not r["true"] for r in revise) / len(revise) - sum(not r["true"] for r in rows) / len(rows)


def _arms(rows: list[dict[str, Any]]) -> dict[str, Any]:
    t = [r for r in rows if r["true"]]
    f = [r for r in rows if not r["true"]]
    gate_tpr, gate_fpr = sum(r["approved"] for r in t) / len(t), sum(r["approved"] for r in f) / len(f)
    revise = [r for r in rows if not r["approved"]]
    approve = [r for r in rows if r["approved"]]
    return {
        "n": len(rows), "true": len(t), "false": len(f),
        "gate": {"approved_true": sum(r["approved"] for r in t), "approved_false": sum(r["approved"] for r in f),
                 "TPR": round(gate_tpr, 3), "FPR": round(gate_fpr, 3)},
        # The corpus is exactly what the non-Manager gates let through, so advisory approves all of it.
        "advisory": {"TPR": 1.0, "FPR": 1.0},
        "delta_TPR": round(1.0 - gate_tpr, 3), "delta_FPR": round(1.0 - gate_fpr, 3),
        "p_false_base": round(len(f) / len(rows), 3),
        "p_false_given_revise": round(sum(not r["true"] for r in revise) / len(revise), 3) if revise else None,
        "p_false_given_approve": round(sum(not r["true"] for r in approve) / len(approve), 3) if approve else None,
        "revise_lift_ci": _cluster_ci(rows, _p_false_given_revise_minus_base),
    }


def c5_corpus() -> dict[str, Any]:
    corpus = {r["id"]: r for r in _jsonl(BENCH / "manager_p/results/corpus.jsonl")}
    verdicts = {r["id"]: r for r in _jsonl(BENCH / "manager_diff/results/manager_diff.jsonl")}
    evidence = {r["id"]: r for r in _jsonl(BENCH / "manager_diff/results/evidence.jsonl")}
    rows = [{"id": i, "task_id": c["task_id"], "true": c["outcome"] >= PASS,
             "approved": verdicts[i]["verdict"] == "approved", "productive": evidence[i]["productive"]}
            for i, c in corpus.items() if i in verdicts and not verdicts[i]["error"]]
    unknown = sorted({v["verdict"] for v in verdicts.values()} - {"approved", "revise"})
    everything = _arms(rows)
    control = (everything["gate"]["approved_true"], everything["true"],
               everything["gate"]["approved_false"], everything["false"]) == (47, 246, 2, 139)
    real = _arms([r for r in rows if r["productive"]])
    return {"control_reproduces_47_246_2_139": control, "unexpected_verdicts": unknown,
            "all_385": everything, "productive_evidence_rows": real}


def main() -> None:
    c5 = c5_corpus()
    if not c5["control_reproduces_47_246_2_139"]:
        raise SystemExit(f"control does not reproduce manager_diff — nothing else is read: {c5['all_385']['gate']}")
    out = {"c1": c1_receipts(), "c2": c2_regimes(), **c3_c4_tool_defer(), "c5": c5}
    d1 = c5["all_385"]["delta_TPR"] >= 0.20 and c5["all_385"]["delta_FPR"] <= 0.05
    d2_identifiable = out["c4"]["reverted_work_recoverable"]
    out["decision"] = {
        "D1_advisory_default": d1,
        "D2_identifiable": d2_identifiable,
        "D2_opt_in": None if not d2_identifiable else "evaluate",
        "ships": "advisory default" if d1 else ("evaluate D2" if d2_identifiable else "nothing"),
    }
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "census.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n",
                                                  encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
