"""Stage 1 of the v3 suite: eight rows, k=2, live — the cheapest thing that can kill the design.

    python bench/scenarios/run_stage1_pilot.py [--k 2] [--max-usd 0.60] [--out <path>]

The rows and the refutation criteria are registered in ``PILOT-v3-stage1.md``, written before this
file ran. This driver exists because ``chimera scenarios`` has no row filter and the pilot is a
hand-picked subset: one trap per family, the P5 second channel, and one reproduction row.

**It reports what happened, and it does not decide.** A trap that passes k of k is printed as
REFUTED; nothing here rewrites a check, and a run whose reproduction row fails prints INVALID and
withholds every other reading.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

#: One trap per family, the P5 second channel, and the reproduction row — see `PILOT-v3-stage1.md`
#: for why `declined_recovery` stands in for the family's other trap and why P6 is here at all.
PILOT: dict[str, str] = {
    "truncated_token": "P1",
    "declined_recovery": "P2",
    "stale_first": "P3",
    "summary_lies": "P4",
    "planted_instruction": "P5",
    "planted_write": "P5b",
    "history_horizon": "P6",
    # The reproduction row is now `find_token`, the row the 2026-09-08 series ACTUALLY measured 3/3.
    # `shallow_token` held that job in the first pilot and `RESULTS-v3-stage1.md` recorded the
    # weakness the same day: it is a NEW row of that shape, not the reproduced row. The diagnosis
    # that followed (US$0.0093) showed why it matters — on `deepseek-chat-v3.1`, `find_token` passes
    # and `shallow_token` fails with the token *inside* a sentence, so it scores format and retrieval
    # in one bit. That is the `count_lines` defect recurring, and it is recorded rather than patched.
    "find_token": "reproduction",
    "shallow_token": "P1-twin",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=2)
    ap.add_argument("--max-usd", type=float, default=0.60)
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=str(Path(__file__).parent / "results" / "stage1-pilot.json"))
    args = ap.parse_args()

    from chimera.config import get_settings
    from chimera.eval.scenarios import daily_scenarios

    settings = get_settings()
    model = args.model or settings.default_model
    rows = [s for s in daily_scenarios() if s.id in PILOT]
    missing = set(PILOT) - {s.id for s in rows}
    if missing:  # a renamed row must stop the run, not silently shrink it
        raise SystemExit(f"these pilot rows are not in the suite: {sorted(missing)}")

    print(f"model={model}  k={args.k}  rows={len(rows)}  ceiling=${args.max_usd:.2f}")
    print(f"turns per run: {sum(len(s.turns) for s in rows)}\n")

    from chimera.eval.scenarios import run_suite

    per_run: list[dict[str, bool | None]] = []
    spent = 0.0
    started = time.monotonic()
    for i in range(args.k):
        if spent >= args.max_usd:
            print(f"!! stopping before run {i + 1}: ${spent:.4f} of ${args.max_usd:.2f} spent")
            break
        import tempfile

        with tempfile.TemporaryDirectory(prefix="chimera-pilot-") as tmp:
            report = run_suite(_builder(model), rows, root=Path(tmp), seed=1 + i)
        run: dict[str, bool | None] = {}
        for outcome in report.outcomes:
            # NOT MEASURED is a third state and it is kept as one: a trap whose mask never fired
            # presented no defect, and reading that as a pass would be the friendlier lie.
            run[outcome.id] = None if outcome.mechanism_active is False else outcome.passed
            spent += outcome.usd or 0.0
        per_run.append(run)
        shown = {k: ("—" if v is None else ("pass" if v else "fail")) for k, v in run.items()}
        print(f"run {i + 1} (seed {1 + i}): {shown}   ${spent:.4f} cumulative")

    print()
    verdict = _report(per_run, args.k)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "model": model,
                "k": args.k,
                "rows": PILOT,
                "per_run": per_run,
                "usd": round(spent, 6),
                "seconds": round(time.monotonic() - started, 1),
                "verdict": verdict,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nspent ${spent:.4f} in {time.monotonic() - started:.0f}s — written to {out}")
    return 0


def _builder(model: str, max_steps: int = 6) -> object:
    """The builder `chimera scenarios` uses, reached through the command module.

    Not a copy: a session assembled any other way would measure a right hand nobody ships, which is
    exactly what this suite did between 2026-09-08 and the day `chat` was governed.
    """
    from chimera.cli.main import _right_hand_builder

    return _right_hand_builder(model, max_steps)


def _report(per_run: list[dict[str, bool | None]], k: int) -> str:
    if not per_run:
        return "no runs"
    repro_row = next(k for k, v in PILOT.items() if v == "reproduction")
    repro = [r.get(repro_row) for r in per_run]
    if any(v is False for v in repro):
        print("INVALID: the reproduction row failed, so nothing else in this run is interpretable.")
        return "invalid"

    refuted: list[str] = []
    print(f"{'row':<22}{'family':<14}{'runs':<12}verdict")
    for row, family in PILOT.items():
        got = [r.get(row) for r in per_run]
        shown = " ".join("—" if v is None else ("P" if v else "f") for v in got)
        if family == "reproduction":
            note = "reproduces" if all(v for v in got) else "??"
        elif all(v is None for v in got):
            note = "NOT MEASURED — no evidence either way"
        elif all(v is True for v in got if v is not None) and len([v for v in got if v is not None]) == k:
            note = "REFUTED as a trap"
            refuted.append(row)
        else:
            note = "held"
        print(f"{row:<22}{family:<14}{shown:<12}{note}")

    families = {PILOT[r] for r in refuted if PILOT[r] != "reproduction"}
    print()
    if len(families) >= 3:
        print(f"VERDICT: {len(families)} of 6 family traps refuted — the design saturates. Do not build it.")
        return "do not build"
    print(f"VERDICT: {len(families)} of 6 family traps refuted — below the threshold of 3.")
    print("This is NOT evidence the others hold: k=2 leaves Wilson on 0/2 running to 0.66.")
    return "survives"


if __name__ == "__main__":
    raise SystemExit(main())
