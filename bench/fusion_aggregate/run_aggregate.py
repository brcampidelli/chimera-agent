"""Union + validator instead of the vote, on the fusion panel — the staged run.

The design, the thresholds and the refutation criterion are fixed in PREREGISTRATION.md, which was
committed before this file ran once. Read that first; this is only the apparatus.

The panel is generated ONCE and cached; every aggregator reads the same answers. That is the whole
design: panel sampling noise is removed by construction, so a difference between aggregators is a
difference between aggregators and not between two draws of the same panel.

    stage 1  generate the panel                                          paid once
    stage 2  oracle / vote_text / vote_answer / member_i, from the cache  FREE, reruns forever
    stage 3  judge_synth / union_validator, on the same cache             paid, GATED on stage 2

`vote_text` is today's shipped vote, which clusters answers by PROSE similarity and returns the
longest member of the winning cluster — so on a logic task it can elect the minority answer, and
three panelists who agree on the number by different reasoning read as no majority at all. Both were
reproduced in the shipped code before this file existed. `vote_answer` is the same rule counting the
extracted number, and it is the baseline stage 3 is measured against: crediting a validator with a
clustering fix would be measuring two interventions and reporting one.

Stage 3 is gated because if `oracle` sits close to `vote_answer`, the pool holds nothing the fixed
vote is missing and no selector can find it. Measuring how much there is to reject before building
the thing that rejects is the rule this project wrote after paying for a retry loop around a
verifier that accepted 95% of what it saw.

Usage:
    python bench/fusion_aggregate/run_aggregate.py --sample 120          # stages 1-2
    python bench/fusion_aggregate/run_aggregate.py --sample 120 --stage3 # adds the paid arms
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(REPO_ROOT / "bench/llm_benchmarks"))

from datasets import load_gsm8k  # noqa: E402

from chimera.eval.paired import compare_paired, format_report  # noqa: E402
from chimera.fusion.consistency import majority  # noqa: E402
from chimera.fusion.task_type import classify_task_type  # noqa: E402

RESULTS = HERE / "results"
PANEL_CACHE = RESULTS / "panel.jsonl"

#: The panel answers a question in the shape the ruler can read. Registered here rather than
#: discovered later: an extractor that has to guess where the number is measures the extractor.
_PROMPT = (
    "Solve this problem. Think it through, then give the final numeric answer on the last line in "
    "exactly this format:\n\nANSWER: <number>\n\nProblem: {question}"
)
_ANSWER_LINE = re.compile(r"ANSWER:\s*(-?[\d,]+(?:\.\d+)?)", re.I)
_ANY_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")

#: Below this share of panel answers yielding the registered line, the ruler is not reading the
#: panel and no arm may be read. `§2e`: a harness written against one model stops reporting the
#: moment the model changes, and it stops silently.
_MIN_EXTRACTION = 0.90


def prereg_sha() -> str:
    path = HERE / "PREREGISTRATION.md"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.is_file() else "MISSING"


def normalise(value: str) -> str:
    """Canonical numeric form, so 1,000 / 1000 / 1000.0 compare equal. Same rule as gsm8k.py."""
    text = str(value).strip().replace(",", "").replace("$", "").rstrip(".")
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number == int(number) else str(number)


def extract(answer: str) -> str | None:
    """The number the panel meant, or None.

    Prefers the registered `ANSWER:` line and falls back to the last number in the text. The
    fallback is measured rather than silent: a run where most answers need it is a run where the
    panel ignored the format, and that is a fact about the apparatus, not about the panel.
    """
    match = _ANSWER_LINE.search(answer)
    if match:
        return normalise(match.group(1))
    numbers = _ANY_NUMBER.findall(answer)
    return normalise(numbers[-1]) if numbers else None


def logic_items(sample: int, seed: int) -> list[dict]:
    """A fixed-seed sample of the GSM8K items the vote branch would actually see.

    Restricted to `classify_task_type == "logic"` on purpose: the vote is what this experiment
    replaces, and it only ever runs there. Measured free before any of this: 856 of 1319 test items
    (64.9%) classify as logic, so the branch is not a rare one on its own corpus.
    """
    rows = [
        row
        for row in load_gsm8k()
        if classify_task_type([{"role": "user", "content": row["question"]}]) == "logic"
    ]
    rows.sort(key=lambda r: r["question"])  # a stable order before the seeded draw
    return random.Random(seed).sample(rows, min(sample, len(rows)))


def gold(row: dict) -> str:
    return normalise(str(row["answer"]).rsplit("####", 1)[-1])


def _key(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]


def _load_cache() -> list[dict]:
    if not PANEL_CACHE.is_file():
        return []
    out = []
    for line in PANEL_CACHE.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def generate_panel(items: list[dict], models: list[str]) -> list[dict]:
    """Stage 1, paid once. One call per (item, model), tool-free, cached to disk.

    Resumable by construction: an item already in the cache is not re-asked, so an interrupted run
    costs what it already spent and nothing more.
    """
    from chimera.providers.gateway import LLMGateway, Message

    RESULTS.mkdir(parents=True, exist_ok=True)
    done = {row["id"] for row in _load_cache()}
    gateway = LLMGateway()
    with PANEL_CACHE.open("a", encoding="utf-8") as cache:
        for index, item in enumerate(items):
            key = _key(item["question"])
            if key in done:
                continue
            answers: list[dict] = []
            for model in models:
                try:
                    result = gateway.complete(
                        [Message(role="user", content=_PROMPT.format(question=item["question"]))],
                        model=model,
                        temperature=0.3,
                    )
                    answers.append(
                        {
                            "model": model,
                            "content": result.content,
                            "prompt_tokens": result.prompt_tokens,
                            "completion_tokens": result.completion_tokens,
                        }
                    )
                except Exception as exc:  # noqa: BLE001 — a dead panelist is data, not a crash
                    answers.append({"model": model, "content": "", "error": str(exc)})
            row = {
                "id": key,
                "question": item["question"],
                "gold": gold(item),
                "answers": answers,
                "prereg": prereg_sha(),
            }
            cache.write(json.dumps(row, ensure_ascii=False) + "\n")
            cache.flush()
            print(f"  [{index + 1}/{len(items)}] {key} gold={row['gold']}")
    return _load_cache()


def union(answers: list[str]) -> list[str]:
    """The DISTINCT candidates: one representative per distinct EXTRACTED ANSWER.

    This is the half the vote throws away, and the grouping is by the number rather than by the
    prose for the reason measured in the pre-registration: `_cluster` compares whole answers with
    difflib, so on a logic task "ANSWER: 41" and "ANSWER: 42" land in one cluster and one of them
    disappears. Grouping by the extracted value is the only grouping that means anything here.

    Order is first-seen, which makes the tie-break in any downstream selector deterministic.
    """
    seen: dict[str, str] = {}
    for answer in answers:
        key = extract(answer) or f"__unparsed_{len(seen)}"
        seen.setdefault(key, answer)
    return list(seen.values())


def vote_answer(answers: list[str]) -> str | None:
    """A strict majority over the extracted NUMBERS — the vote the logic branch was meant to be.

    Same rule as `majority`: the winning group must hold MORE than half, so a plurality does not
    win and there can be no tie. What changes is what is being counted.
    """
    picked = [extract(a) for a in answers]
    live = [p for p in picked if p]
    if not live:
        return None
    counts: dict[str, int] = {}
    for value in live:
        counts[value] = counts.get(value, 0) + 1
    best, hits = max(counts.items(), key=lambda kv: kv[1])
    return best if hits * 2 > len(picked) else None


def arms_free(rows: list[dict]) -> dict[str, list[bool]]:
    """Stage 2. Everything computable from the cache: the ceiling, the floor, and today's vote."""
    out: dict[str, list[bool]] = {"oracle": [], "vote_text": [], "vote_answer": []}
    models = [a["model"] for a in rows[0]["answers"]] if rows else []
    for model in models:
        out[f"member:{model.split('/')[-1]}"] = []
    for row in rows:
        golden = row["gold"]
        texts = [a["content"] for a in row["answers"] if not a.get("error") and a["content"]]
        picked = [p for p in (extract(t) for t in texts) if p]
        # The ceiling: does ANY panel member hold the right answer? Everything an aggregator could
        # ever win lives between this line and the best single member.
        out["oracle"].append(golden in picked)
        # Today's shipped behaviour, faithfully: a majority over TEXT similarity, whose winner is
        # the longest member of the winning cluster. Reported so the size of the bug is visible
        # beside the fix rather than folded into it.
        winner = majority(texts) if texts else None
        out["vote_text"].append(bool(winner) and extract(winner) == golden)
        # The same rule, counting the number instead of the prose. This is the baseline criterion 1
        # is measured against: crediting a validator with a clustering fix would be measuring two
        # interventions and reporting one.
        out["vote_answer"].append(vote_answer(texts) == golden)
        for answer in row["answers"]:
            key = f"member:{answer['model'].split('/')[-1]}"
            got = extract(answer["content"]) if answer.get("content") else None
            out[key].append(got == golden)
    return out


def extraction_rate(rows: list[dict]) -> float:
    """The share of live panel answers that carried the registered ANSWER: line."""
    total = ok = 0
    for row in rows:
        for answer in row["answers"]:
            if answer.get("error"):
                continue
            total += 1
            ok += int(_ANSWER_LINE.search(answer.get("content") or "") is not None)
    return ok / total if total else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=120)
    parser.add_argument("--seed", type=int, default=20260828)
    parser.add_argument("--stage3", action="store_true", help="Run the paid aggregators.")
    args = parser.parse_args()

    from chimera.config import get_settings

    settings = get_settings()
    models = list(settings.fusion_panel)
    print(f"prereg={prereg_sha()}  sample={args.sample}  seed={args.seed}")
    print(f"panel={models}")

    items = logic_items(args.sample, args.seed)
    print(f"logic-typed GSM8K items drawn: {len(items)}")
    rows = generate_panel(items, models)
    keys = {_key(item["question"]) for item in items}
    rows = [row for row in rows if row["id"] in keys]
    print(f"panel cached for {len(rows)} items")

    rate = extraction_rate(rows)
    print(f"extraction: {rate:.1%} of answers carried the registered ANSWER: line")
    if rate < _MIN_EXTRACTION:
        # A ruler that reads the wrong number reports the panel as wrong, and the arm that happens
        # to phrase itself the ruler's way wins for a reason that has nothing to do with the panel.
        raise SystemExit(
            f"STOP: only {rate:.1%} of panel answers used the registered format (floor "
            f"{_MIN_EXTRACTION:.0%}). The ruler is not reading the panel; fix it before reading arms."
        )

    free = arms_free(rows)
    print()
    for name, hits in sorted(free.items()):
        print(f"  {name:<34} {sum(hits) / len(hits):6.1%}  ({sum(hits)}/{len(hits)})")

    headroom = (sum(free["oracle"]) - sum(free["vote"])) / len(rows) * 100
    print(f"\nHEADROOM (oracle - vote) = {headroom:+.1f} pp")
    print(
        format_report(
            compare_paired(free["vote"], free["oracle"], baseline_name="vote", treatment_name="oracle")
        )
    )

    if headroom < 3.0:
        print("=" * 78)
        print("STOP, by the rule fixed in the pre-registration: the pool holds nothing the FIXED")
        print("vote is missing, so no selector can find it. Stage 3 is not run, and the honest")
        print("outcome of this item is the clustering fix on its own.")
        return
    if not args.stage3:
        print("=" * 78)
        print(f"Headroom is {headroom:+.1f} pp, above the 3.0 pp gate. Re-run with --stage3 to pay")
        print("for the two aggregators. Nothing above is a comparison between aggregators yet.")
        return

    raise SystemExit("stage 3 is not built yet: build it only if the headroom above justifies it")


if __name__ == "__main__":
    main()
