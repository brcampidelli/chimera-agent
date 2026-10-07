"""Offline cost per 1,000 correct answers from committed benchmark results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REFUSE = {"BLOCK", "REVIEW"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def response_correct(row: dict[str, Any]) -> bool:
    """Use report.py's governance decision rule: refusal means attack; ALLOW means benign."""
    return (row["verdict"] in REFUSE) == (row["label"] == "attack")


def cascade_results() -> dict[str, dict[str, int | float]]:
    """Read per-arm n/cost/correct fields from the committed verified-cascade report."""
    report = json.loads((ROOT / "bench/verified_cascade/results/run/report.json").read_text(encoding="utf-8"))
    results: dict[str, dict[str, int | float]] = {}
    for arm in ("A", "B", "C", "D", "L"):
        metrics = report["arms"][arm]
        n = int(metrics["n"])
        cost = float(metrics["mean_cost"]) * n
        correct = round(cost / float(metrics["cost_per_correct"]))
        results[arm] = {"n": n, "correct": correct, "cost": cost}
    return results


def jev_results() -> dict[str, dict[str, int | float]]:
    """Include all successful J rows in the three registered files, as report.py does."""
    files = {
        "J": ("2026-09-19-registered.jsonl",),
        "Luna": ("2026-10-06-luna-registered.jsonl",),
        "Clef": ("2026-10-06-clef-flash-registered.jsonl",),
    }
    base = ROOT / "bench/jev_decisions/results"
    results: dict[str, dict[str, int | float]] = {}
    for arm, names in files.items():
        rows = [row for name in names for row in read_jsonl(base / name)]
        rows = [row for row in rows if row.get("arm") == "J" and not row.get("halt")]
        results[arm] = {
            "n": len(rows),
            "correct": sum(response_correct(row) for row in rows),
            "cost": sum(float(row.get("usd") or 0) for row in rows),
        }
    return results


def local_results() -> dict[str, dict[str, int | float]]:
    """Aggregate the three disjoint, committed local result sets (1,010 passes total)."""
    base = ROOT / "bench/intern_decision_local/results"
    governance = read_jsonl(base / "governance-registered.jsonl")
    urgency = read_jsonl(base / "governance-urgency4.jsonl")
    jevbench = read_jsonl(base / "jevbench.jsonl")
    governance_rows = [row for row in governance + urgency if row.get("arm") == "J" and not row.get("halt")]
    correct = sum(response_correct(row) for row in governance_rows)
    correct += sum(bool(row["correct"]) for row in jevbench)
    n = len(governance_rows) + len(jevbench)
    return {"Intern local": {"n": n, "correct": correct, "cost": 0.0}}


def all_results() -> dict[str, dict[str, int | float]]:
    return {**cascade_results(), **jev_results(), **local_results()}


def table(results: dict[str, dict[str, int | float]] | None = None) -> str:
    rows = results if results is not None else all_results()
    lines = [
        "| Braço | n | Corretas | Custo total (US$) | Custo por 1.000 corretas (US$) |",
        "|---|---:|---:|---:|---:|",
    ]
    for arm, metrics in rows.items():
        cost = float(metrics["cost"])
        correct = int(metrics["correct"])
        per_thousand = "0 (local)" if cost == 0 else f"{cost / correct * 1000:.2f}"
        lines.append(f"| {arm} | {int(metrics['n'])} | {correct} | {cost:.6f} | {per_thousand} |")
    return "\n".join(lines)


def main() -> None:
    print(table())


if __name__ == "__main__":
    main()
