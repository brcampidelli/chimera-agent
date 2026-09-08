"""Authorization-equivalence pairs (arXiv 2608.29942), run against the shipped ledger. US$ 0.

    python bench/injection/run_authorization.py [--out bench/injection/results/<date>-authorization.txt]

Registered in `bench/injection/PREREGISTRATION.md` (2026-09-08 section). Every row runs the SAME
`LedgeredTool(narrow_on_taint=True)` the injection bench uses; the three rows of a triple carry the
same action, args and effect and differ only in where the value came from — the user's message
(USER), a tool result the user asked for (LEGIT_TOOL), or untrusted content (UNTRUSTED). The
question is whether the gate can tell the middle one from the last one.

Two approvers, so "escalated" is not confused with "hard-blocked":

* **unattended** — `approve=None`: the shipped headless default. An escalation becomes a refusal.
* **approves** — `allow`: an escalated row runs, proving the ledger escalated it rather than blocked
  it outright (the ledger only ever escalates to review).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from chimera.eval.authorization import (  # noqa: E402
    EquivalenceReport,
    ValueSource,
    default_triples,
    run_equivalence,
)
from chimera.governance.approval import ApprovalLedger, allow  # noqa: E402

_SOURCES = (ValueSource.USER, ValueSource.LEGIT_TOOL, ValueSource.UNTRUSTED)
_AUTHORIZED = "authorized by the user"


def _three_way(report: EquivalenceReport) -> str:
    lines = ["  3-way, per source (escalated = the gate sent it to review):"]
    lines.append(
        f"    {'source':<12} {'n':>3} {'allowed':>8} {'escalated':>10} {'refused':>8}  note"
    )
    for source in _SOURCES:
        rows = report.by_source(source)
        allowed = sum(o.executed for o in rows)
        escalated = sum(o.escalated for o in rows)
        refused = sum(o.refused for o in rows)
        note = _AUTHORIZED if source is not ValueSource.UNTRUSTED else "the attack"
        lines.append(
            f"    {source.value:<12} {len(rows):>3} {allowed:>8} {escalated:>10} {refused:>8}  {note}"
        )
    return "\n".join(lines)


def _per_action(report: EquivalenceReport) -> str:
    actions = sorted({o.action for o in report.outcomes})
    lines = ["  per action type — escalated / n, by source:"]
    lines.append(f"    {'action':<14} {'USER':>10} {'LEGIT_TOOL':>12} {'UNTRUSTED':>12}")
    for action in actions:
        cells = []
        for source in _SOURCES:
            rows = [o for o in report.by_source(source) if o.action == action]
            cells.append(f"{sum(o.escalated for o in rows)}/{len(rows)}")
        lines.append(f"    {action:<14} {cells[0]:>10} {cells[1]:>12} {cells[2]:>12}")
    return "\n".join(lines)


def _per_row(report: EquivalenceReport) -> str:
    lines = ["  per row (triple · source · mechanism · outcome under auto-deny):"]
    for o in report.outcomes:
        mech = "narrow" if o.narrowed else ("assess" if o.assessed else "-")
        outcome = "REFUSED" if o.refused else "ran"
        lines.append(
            f"    {o.triple_id:<38} {o.source.value:<11} {o.action:<12} "
            f"tainted={str(o.tainted):<5} esc={str(o.escalated):<5} {mech:<7} {outcome}"
        )
    return "\n".join(lines)


def _fp(report: EquivalenceReport) -> str:
    fp = report.false_positive_rate()
    return (
        "  false-positive rate on LEGITIMATE flows (the number the prediction asks for):\n"
        f"    USER-sourced       : {fp['fp_user']:.3f}  (n={int(fp['n_user'])})\n"
        f"    LEGIT_TOOL-sourced : {fp['fp_legit_tool']:.3f}  (n={int(fp['n_legit_tool'])})   <- the cost\n"
        f"    pooled legit       : {fp['fp_pooled_legit']:.3f}  (n={int(fp['n_user'] + fp['n_legit_tool'])})   (labelled; do not average heterogeneous rows — §2y)\n"
        f"    UNTRUSTED (true +) : {fp['tp_untrusted']:.3f}  (n={int(fp['n_untrusted'])})"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    out: list[str] = []
    triples = default_triples()

    def section(title: str, text: str) -> None:
        block = f"## {title}\n{text}\n"
        out.append(block)
        print(block)

    section(
        "corpus",
        f"  {len(triples)} rows = {len(triples) // 3} triples x 3 sources "
        f"({len({r.triple_id for r in triples})} distinct committed actions)",
    )

    unattended = run_equivalence(triples, approve=None)
    section(
        "unattended (approve=None — the shipped headless default)",
        _three_way(unattended) + "\n" + _per_action(unattended) + "\n" + _fp(unattended),
    )
    section("per row (unattended)", _per_row(unattended))

    approved = run_equivalence(triples, approve=allow(ApprovalLedger()))
    section(
        "approver says yes (allow — proves escalation, not hard block)",
        _three_way(approved),
    )

    if args.out:
        Path(args.out).write_text("\n".join(out), encoding="utf-8")
        print(f"  written {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
