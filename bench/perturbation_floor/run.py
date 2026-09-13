"""Measure both halves of the judge's floor in one session. Registered in PREREGISTRATION.md.

    python bench/perturbation_floor/run.py --run [--model <slug>] [--corpus easy|ambiguous]
    python bench/perturbation_floor/run.py --report results/<tag>.jsonl

The judge, the prompt and the call are `bench/governance_judge`'s own — imported, not copied, so the
replay number this run re-measures is comparable to the 19/19 and 29/34 that bench published. Every
call is bounded and an empty reply twice is a halt that leaves the denominator, never a disagreement.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from bench.governance_judge.corpus import corpus as corpus_easy  # noqa: E402
from bench.governance_judge.corpus_ambiguous import corpus as corpus_ambiguous  # noqa: E402
from bench.governance_judge.run import _judge_word  # noqa: E402
from bench.perturbation_floor.perturb import rewrites  # noqa: E402
from chimera.config import get_settings  # noqa: E402
from chimera.governance.governed_tool import render_action  # noqa: E402

_CORPORA = {"easy": corpus_easy, "ambiguous": corpus_ambiguous}
HERE = Path(__file__).resolve().parent

#: What `bench/governance_judge/RESULTS.md` published, for the paired check of PROTOCOL §2aa.
PUBLISHED_REPLAY = {"easy": 19 / 19, "ambiguous": 29 / 34}


def _action(command: str) -> str:
    action, _document = render_action("run_shell", {"command": command})
    return action


def run(out: Path, model: str, corpus_name: str) -> None:
    from chimera.providers import LLMGateway

    gateway = LLMGateway()
    items = _CORPORA[corpus_name]()
    spent = {"usd": 0.0, "calls": 0, "halts": 0}

    def ask(command: str) -> str | None:
        word, usd = _judge_word(gateway, model, _action(command))
        spent["usd"] += usd or 0.0
        spent["calls"] += 1
        if word is None:
            spent["halts"] += 1
        return word

    rows: list[dict[str, Any]] = []
    for index, item in enumerate(items, 1):
        first = ask(item.command)
        second = ask(item.command)  # the replay half, in THIS session
        produced = []
        for rewrite in rewrites(item.command):
            produced.append({
                "kind": rewrite.kind,
                "after": rewrite.after,
                "verdict": ask(rewrite.after),
            })
        rows.append({
            "id": item.id, "label": item.label, "command": item.command,
            "first": first, "replay": second, "rewrites": produced,
        })
        # Built outside the f-string: nesting the same quote character inside one is PEP 701,
        # which is 3.12+, and this project targets 3.11.
        shown = " ".join(f"{p['kind']}={p['verdict']}" for p in produced)
        print(f"  [{index:>2}/{len(items)}] {item.id:<28} {first} / {second} | {shown}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"model": model, "corpus": corpus_name, "spent": spent, "rows": rows},
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )
    print(f"\nwrote {out}  —  {spent['calls']} calls, US${spent['usd']:.4f}, {spent['halts']} halts")


def report(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows, corpus_name = payload["rows"], payload["corpus"]

    # Replay: the item asked twice. A halt on either side leaves the denominator (PROTOCOL §2).
    replay_pairs = [(r["first"], r["replay"]) for r in rows if r["first"] and r["replay"]]
    replay_same = sum(1 for a, b in replay_pairs if a == b)

    # Paraphrase: the item against each neutral rewrite of it.
    para: list[tuple[str, str, str, str]] = []
    for row in rows:
        if not row["first"]:
            continue
        for rewrite in row["rewrites"]:
            if rewrite["verdict"]:
                para.append((row["id"], rewrite["kind"], row["first"], rewrite["verdict"]))
    para_same = sum(1 for _, _, a, b in para if a == b)

    print(f"\n=== {corpus_name} — {payload['model']} ===")
    print(f"  replay floor      {replay_same}/{len(replay_pairs)} = "
          f"{replay_same / len(replay_pairs):.3f}   (published: {PUBLISHED_REPLAY[corpus_name]:.3f})")
    print(f"  paraphrase floor  {para_same}/{len(para)} = {para_same / len(para):.3f}")

    by_kind: Counter[str] = Counter()
    moved_by_kind: Counter[str] = Counter()
    for _, kind, a, b in para:
        by_kind[kind] += 1
        if a != b:
            moved_by_kind[kind] += 1
    print("\n  per rewrite (moved / asked) — counts, not rates: two of these fire too few times")
    for kind, total in sorted(by_kind.items()):
        print(f"    {kind:14s} {moved_by_kind[kind]:>2} / {total:>2}")

    moved = [(i, k, a, b) for i, k, a, b in para if a != b]
    if moved:
        print("\n  every verdict that moved:")
        for ident, kind, a, b in moved:
            print(f"    {ident:<30} {kind:14s} {a} -> {b}")
    print(f"\n  spent US${payload['spent']['usd']:.4f} over {payload['spent']['calls']} calls "
          f"({payload['spent']['halts']} halts)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--model", default=get_settings().fusion_judge)
    ap.add_argument("--corpus", choices=("easy", "ambiguous"), default="easy")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    if args.run:
        out = args.out or HERE / "results" / f"{args.corpus}.json"
        run(out, args.model, args.corpus)
        report(out)
    elif args.report:
        report(args.report)
    else:
        ap.error("one of --run or --report")


if __name__ == "__main__":
    main()
