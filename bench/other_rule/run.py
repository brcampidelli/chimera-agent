"""M4 — the catch-all option, three arms on the frozen commit subjects. See PREREGISTRATION.md.

    python -m bench.other_rule.run            # all three arms, resumable
    python -m bench.other_rule.run --read     # the registered outcomes

Local only, US$ 0: the product path (`build_decider` with the configured local backend, and the same
`decide` the CLI and the route call), no decision log, one row per item per arm in results/<arm>.jsonl.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
ITEMS = ROOT / "bench" / "decide_interface" / "results" / "items.jsonl"
ITEMS_SHA16 = "9eef77f7f3fb0c23"
NAMED = ("feature", "fix", "docs", "test")
INSTRUCTIONS = (
    "You read the subject line of a commit in a software repository. What kind of change does the commit make?"
)
NAMED_CRITERIA = {
    "feature": "adds a capability, a command, a screen or an option that did not exist",
    "fix": "corrects behaviour that was wrong",
    "docs": "changes documentation only",
    "test": "changes tests only",
}
ARMS: dict[str, dict[str, str]] = {
    "current": {**NAMED_CRITERIA, "other": "anything else: a benchmark, a refactor, a build or release chore, a dependency bump"},
    "narrow": {**NAMED_CRITERIA, "other": "only when the subject plainly names a benchmark, a refactor, a build or release chore, "
                                          "or a dependency bump — never a subject that adds or corrects something"},
    "none": dict(NAMED_CRITERIA),
}
SEED, DRAWS = 7, 2000


def items() -> list[dict[str, str]]:
    raw = ITEMS.read_bytes()
    if hashlib.sha256(raw).hexdigest()[:16] != ITEMS_SHA16:
        raise SystemExit("items.jsonl is not the pre-registered file")
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]


def run() -> None:
    from chimera.config import get_settings
    from chimera.decisions.factory import build_decider
    from chimera.decisions.interface import decide

    decider = build_decider(get_settings(), log=False)
    RESULTS.mkdir(parents=True, exist_ok=True)
    rows = items()
    for arm, criteria in ARMS.items():
        out = RESULTS / f"{arm}.jsonl"
        done = {json.loads(x)["id"] for x in out.read_text(encoding="utf-8").splitlines() if x.strip()} if out.exists() else set()
        body_q = {"kind": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": criteria}}
        with out.open("a", encoding="utf-8") as sink:
            for n, item in enumerate(rows, 1):
                if item["id"] in done:
                    continue
                answer = decide(decider, {"state": item["state"], "questions": body_q, "decision": f"m4.{arm}"})["answers"]["kind"]
                sink.write(json.dumps({"id": item["id"], "truth": item["truth"], "answer": answer.get("choice"),
                                       "error": answer.get("error"), "probabilities": answer.get("probabilities")}, ensure_ascii=False) + "\n")
                sink.flush()
                if n % 100 == 0:
                    print(f"{arm}: {n}/{len(rows)}", flush=True)
        print(f"{arm}: done", flush=True)


def macro_f1(rows: list[dict[str, Any]]) -> float:
    f1s = []
    for c in NAMED:
        tp = sum(r["truth"] == c and r["answer"] == c for r in rows)
        fp = sum(r["truth"] != c and r["answer"] == c for r in rows)
        fn = sum(r["truth"] == c and r["answer"] != c for r in rows)
        f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    return sum(f1s) / len(f1s)


def read() -> None:
    arms = {a: {r["id"]: r for r in (json.loads(x) for x in (RESULTS / f"{a}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip())}
            for a in ARMS}
    ids = [i["id"] for i in items()]
    named = [i for i in ids if arms["current"][i]["truth"] in NAMED]
    others = [i for i in ids if arms["current"][i]["truth"] == "other"]
    print(f"items {len(ids)} · named {len(named)} · other {len(others)} · errors "
          + ", ".join(f"{a} {sum(1 for r in arms[a].values() if r['error'])}" for a in ARMS))
    for a in ARMS:
        rows = [arms[a][i] for i in named]
        acc = sum(r["answer"] == r["truth"] for r in rows) / len(rows)
        rec = {c: sum(r["answer"] == c for r in rows if r["truth"] == c) / max(1, sum(r["truth"] == c for r in rows)) for c in ("feature", "fix")}
        kept = sum(arms[a][i]["answer"] == "other" for i in others) / len(others)
        overall = sum(arms[a][i]["answer"] == arms[a][i]["truth"] for i in ids) / len(ids)
        print(f"{a:<8} macro-F1 {macro_f1(rows):.3f} · named acc {acc:.3f} · recall feature {rec['feature']:.3f} fix {rec['fix']:.3f}"
              f" · other items kept as other {kept:.3f} · overall acc {overall:.3f}")
    rng = random.Random(SEED)
    base = [arms["current"][i] for i in named]
    for a in ("narrow", "none"):
        arm_rows = [arms[a][i] for i in named]
        point = macro_f1(arm_rows) - macro_f1(base)
        draws = []
        for _ in range(DRAWS):
            idx = [rng.randrange(len(named)) for _ in named]
            draws.append(macro_f1([arm_rows[j] for j in idx]) - macro_f1([base[j] for j in idx]))
        draws.sort()
        lo, hi = draws[int(0.025 * DRAWS)], draws[int(0.975 * DRAWS) - 1]
        print(f"Δ macro-F1 {a} − current = {point:+.3f}  95% CI [{lo:+.3f}, {hi:+.3f}]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--read", action="store_true")
    args = parser.parse_args()
    read() if args.read else run()
