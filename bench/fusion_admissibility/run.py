"""Zero-cost replay of member agreement and fusion early-stop rules (S30-52)."""

from __future__ import annotations

import difflib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
HARD = REPO / "bench/judge_blind_hard/results/collect-all.jsonl"
EASY = REPO / "bench/fusion_aggregate/results/panel.jsonl"
sys.path.insert(0, str(REPO))

ANSWER = re.compile(r"ANSWER:\s*([^\n\r]+)", re.IGNORECASE)
PHRASING_THRESHOLD = 0.8


def _norm_answer(answer: str) -> str:
    return " ".join(answer.casefold().split()).rstrip(".")


def _answer(text: str) -> str | None:
    match = ANSWER.search(text)
    return _norm_answer(match.group(1)) if match else None


def _kappa(a: list[bool], b: list[bool]) -> float | None:
    if not a:
        return None
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / len(a)
    pa = sum(a) / len(a)
    pb = sum(b) / len(b)
    expected = pa * pb + (1 - pa) * (1 - pb)
    return None if expected == 1 else (observed - expected) / (1 - expected)


def _correlation(a: list[bool], b: list[bool]) -> float | None:
    x = [not value for value in a]
    y = [not value for value in b]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    vx = sum((value - mx) ** 2 for value in x)
    vy = sum((value - my) ** 2 for value in y)
    if not vx or not vy:
        return None
    return sum((u - mx) * (v - my) for u, v in zip(x, y, strict=True)) / math.sqrt(vx * vy)


def _stats(correct: list[list[bool]], names: list[str]) -> dict[str, Any]:
    pairs = []
    for i, left in enumerate(correct):
        for j in range(i + 1, len(correct)):
            right = correct[j]
            pairs.append({
                "members": [names[i], names[j]],
                "kappa_correctness": _kappa(left, right),
                "error_correlation": _correlation(left, right),
            })
    correlations = [
        pair["error_correlation"]
        for pair in pairs
        if isinstance(pair["error_correlation"], float)
    ]
    return {
        "n_items": len(correct[0]) if correct else 0,
        "members": names,
        "pairs": pairs,
        "mean_pairwise_error_correlation": sum(correlations) / len(correlations) if correlations else None,
    }


def _rule_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fired = {"phrasing": 0, "answer_equal": 0}
    correct = {"phrasing": 0, "answer_equal": 0}
    missing_answer = 0
    for row in rows:
        answers = row["answers"][:2]
        if len(answers) < 2 or any(not text.strip() for text in answers):
            continue
        phrase_match = difflib.SequenceMatcher(
            None, " ".join(answers[0].split()).lower(), " ".join(answers[1].split()).lower()
        ).ratio() >= PHRASING_THRESHOLD
        parsed = [_answer(text) for text in answers]
        if any(value is None for value in parsed):
            missing_answer += 1
        answer_match = parsed[0] is not None and parsed[0] == parsed[1]
        for rule, match in (("phrasing", phrase_match), ("answer_equal", answer_match)):
            if match:
                fired[rule] += 1
                # Accuracy here means both probe members independently returned the reference.
                correct[rule] += all(row["correct"][:2])
    n = len(rows)
    return {
        rule: {
            "fired": fired[rule],
            "items": n,
            "firing_rate": fired[rule] / n if n else None,
            "precision": correct[rule] / fired[rule] if fired[rule] else None,
            "estimated_member_calls_saved_best_of_3": fired[rule],
            "estimated_total_calls_saved_best_of_3": fired[rule] * 2,
        }
        for rule in fired
    } | {"missing_or_unextractable_answers_in_probes": missing_answer, "phrasing_threshold": PHRASING_THRESHOLD}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def replay_readout() -> dict[str, Any]:
    rows = _read_jsonl(HARD)
    writers = rows[0]["writers"]
    hard_correct = [
        [bool(row["correct"][member]) for row in rows]
        for member in range(len(writers))
    ]
    published_panel = _read_json(REPO / "bench/panel_correlation/results.json")
    hard_icc = next(item["icc"] for item in published_panel if item["items"] == 50)
    out: dict[str, Any] = {
        "readout_version": 1,
        "cost_usd": 0,
        "model_calls": 0,
        "sources": [str(HARD.relative_to(REPO)), "bench/panel_correlation/results.json"],
        "hard_member_admissibility": _stats(hard_correct, writers),
        "hard_published_icc1": hard_icc,
        "hard_agreement_rules": _rule_metrics(rows),
        "panel_size": len(writers),
        "probe_size": 2,
    }
    if EASY.is_file():
        easy_rows = _read_jsonl(EASY)
        names = sorted({answer["model"] for row in easy_rows for answer in row["answers"]})
        correct: dict[str, list[bool]] = {name: [] for name in names}
        for row in easy_rows:
            by_name = {answer["model"]: answer for answer in row["answers"]}
            for name in names:
                text = by_name[name].get("content", "")
                found = ANSWER.search(text)
                correct[name].append(bool(found and _norm_answer(found.group(1)) == _norm_answer(row["gold"])))
        out["sources"].append(str(EASY.relative_to(REPO)))
        out["aggregate_member_admissibility"] = _stats([correct[name] for name in names], names)
    else:
        out["aggregate_member_admissibility"] = None
        out["aggregate_source_status"] = "missing: fusion_aggregate/results is gitignored and absent"
    return out


def main() -> None:
    out = replay_readout()
    dest = Path(__file__).resolve().parent / "readout.json"
    dest.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(out, indent=2, sort_keys=True))
    print(f"wrote {dest.relative_to(REPO)}")


if __name__ == "__main__":
    main()
