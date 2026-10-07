"""Paired offline-seed replay for candidate visibility; live calls are explicit and local-only."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chimera.eval import proportions  # noqa: E402

SEED = ROOT / "bench/judge_blind_hard/results/collect-all.jsonl"
JUDGE_ANALYSIS = (
    "Consider the candidate answers, check their reasoning against the task, and give the best "
    "supported answer. Do not assume any candidate is correct."
)
SYNTH_SYSTEM = (
    "You are a synthesizer. Using the original task and the judge's structured "
    "analysis of several candidate answers, write the single best final answer. "
    "Resolve contradictions, fold in unique insights, and avoid the blind spots. "
    "Answer the task directly; do not mention the panel or the judge."
)


def extract(text: str) -> str | None:
    """Use the seed AIME benchmark's numeric extraction/normalisation convention."""
    path = ROOT / "bench/llm_benchmarks/gsm8k.py"
    sys.path.insert(0, str(path.parent))
    from gsm8k import extract_answer, normalise

    value = extract_answer(text)
    return normalise(value) if value else None


def cohort(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    selected = []
    for row in rows:
        answers, correct = row.get("answers"), row.get("correct")
        if (isinstance(answers, list) and isinstance(correct, list) and len(answers) == len(correct) == 3
                and all(isinstance(a, str) for a in answers)
                and all(isinstance(c, bool) for c in correct) and any(correct) and not all(correct)):
            selected.append(row)
    return selected, len(rows) - len(selected)


def prompts(row: dict[str, Any]) -> dict[str, str]:
    task = f"Original task and context:\nuser: {row['question']}\n\nJudge's analysis:\n{JUDGE_ANALYSIS}"
    # The production renderer's blind form (``blind_panel`` is on by default), with the shown order
    # fixed to writer order so the instrument is deterministic: no vendor slug, letters A/B/C.
    candidates = "\n\n".join(
        f"--- Answer {chr(ord('A') + i)} ---\n{text}" for i, text in enumerate(row["answers"])
    )
    return {"A_as_sent": task, "B_candidates_visible": task + "\n\nCandidate answers:\n" + candidates}


def _call_local(endpoint: str, model: str, prompt: str) -> str:
    url = endpoint.rstrip("/") + "/chat/completions"
    body = json.dumps({"model": model, "temperature": 0, "max_tokens": 16000,
                       "messages": [{"role": "system", "content": SYNTH_SYSTEM},
                                   {"role": "user", "content": prompt}]}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"local endpoint call failed: {exc}") from exc
    text = str(result["choices"][0]["message"].get("content") or "")
    if not text.strip():
        # qwen3 can spend the whole budget in its reasoning channel and return no content. That is
        # an instrument failure, not a wrong answer: scoring it as incorrect would put the
        # apparatus's misses into the regression rate.
        raise RuntimeError("empty completion from the local endpoint: instrument error, not an answer")
    return text


def replay(rows: list[dict[str, Any]], call: Callable[[str], str], *, model: str) -> list[dict[str, Any]]:
    selected, excluded = cohort(rows)
    if not selected:
        raise ValueError("no headroom-qualified cohort rows")
    outputs = []
    for index, row in enumerate(selected):
        pair = prompts(row)
        order = ("A_as_sent", "B_candidates_visible") if index % 2 == 0 else (
            "B_candidates_visible", "A_as_sent")
        texts: dict[str, str] = {}
        for arm in order:
            texts[arm] = call(pair[arm])
        results = {}
        for arm, text in texts.items():
            answer = extract(text)
            results[arm] = {"output": text, "extracted": answer,
                            "correct": answer == str(row["reference"])}
        outputs.append({"item_id": row["item_id"], "reference": str(row["reference"]),
                        "best_candidate_correct": True, "judge_analysis": JUDGE_ANALYSIS,
                        "order": list(order), "arms": results,
                        "excluded_source_rows": excluded if index == 0 else 0, "model": model})
    return outputs


def summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(results)
    correct_a = sum(bool(r["arms"]["A_as_sent"]["correct"]) for r in results)
    correct_b = sum(bool(r["arms"]["B_candidates_visible"]["correct"]) for r in results)
    a_only = sum(r["arms"]["A_as_sent"]["correct"] and not r["arms"]["B_candidates_visible"]["correct"] for r in results)
    b_only = sum(not r["arms"]["A_as_sent"]["correct"] and r["arms"]["B_candidates_visible"]["correct"] for r in results)
    # One home for the arithmetic (chimera/eval/proportions.py).
    p_value = proportions.mcnemar_exact(a_only, b_only)
    return {"n": n, "regression_rate_A": 1 - correct_a / n, "regression_rate_B": 1 - correct_b / n,
            "regression_reduction_A_minus_B_pp": 100 * (correct_b - correct_a) / n,
            "accuracy_A": correct_a / n, "accuracy_B": correct_b / n,
            "accuracy_difference_B_minus_A_pp": 100 * (correct_b - correct_a) / n,
            "mcnemar_discordant_A_wrong_B_right": b_only, "mcnemar_discordant_A_right_B_wrong": a_only,
            "mcnemar_exact_two_sided_p": p_value,
            "missing_or_unparseable": sum(r["arms"][arm]["extracted"] is None
                                           for r in results for arm in ("A_as_sent", "B_candidates_visible"))}


def run(endpoint: str, model: str, output: Path, *, call: Callable[[str], str] | None = None) -> None:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    rows = [json.loads(line) for line in SEED.read_text(encoding="utf-8").splitlines() if line.strip()]
    selected, excluded = cohort(rows)
    results = replay(rows, call or (lambda prompt: _call_local(endpoint, model, prompt)), model=model)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        for row in results:
            row["usd"] = 0.0
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        stream.write(json.dumps({"summary": summary(results), "source_rows": len(rows),
                                 "excluded_rows": excluded,
                                 "cohort_item_ids": [row["item_id"] for row in selected],
                                 "expected_usd": 0.0}, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint", required=True, help="Local Ollama OpenAI-compatible endpoint")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.endpoint.startswith(("http://localhost", "http://127.0.0.1", "http://[::1]")):
        parser.error("endpoint must be local (localhost, 127.0.0.1, or ::1)")
    run(args.endpoint, args.model, Path(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
