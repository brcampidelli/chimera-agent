"""The arms of `PREREGISTRATION.md` (2026-09-08, authority section), run against the shipped ledger.

    python bench/injection/run_authority.py [--out-dir bench/injection/results] [--tag 2026-09-08-authority]

Both benches, both modes, US$ 0 — stub tools, no model. Each mode is passed to the harness
explicitly (`authority=`), so a run does not depend on what `CHIMERA_TAINT_AUTHORITY` happens to be
in the environment it runs in; what it measures is the ledger under each mode.

* **authorization triples** — `chimera.eval.authorization`, the ten matched triples of the run above,
  under `provenance` and under `authority`.
* **injection, standard rows** — `chimera.eval.injection` exactly as shipped: the corpus sets no
  instruction, so every fetch reads `unknown`, and the two modes are predicted byte-identical.
* **injection, the user asked for the page** — the same corpus with `user_requested=True`: the
  instruction names the page and the poisoned fetch is recorded under that page's URL. This is the
  whole question, and the pre-registration predicts six of seven attacks slip under `authority`.

The last section lists every attack row whose verdict differs between the modes, with the mechanism
that decided it, because a block rate that moved is a number and the rows are the finding.
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
from chimera.eval.injection import (  # noqa: E402
    BenignReport,
    RedTeamReport,
    default_attacks,
    default_benign,
    run_benign,
    run_redteam,
)
from chimera.governance.approval import ApprovalLedger, allow  # noqa: E402

MODES = ("provenance", "authority")
_SOURCES = (ValueSource.USER, ValueSource.LEGIT_TOOL, ValueSource.UNTRUSTED)


def _mechanism(detail: str) -> str:
    """Which gate decided a refusal, read off the refusal text the wrapper returns.

    The harness keeps the first 80 characters of the result, and the narrowing's sentence is cut
    mid-way there for a ten-letter tool name — the first version of this matched the whole sentence
    and labelled every narrowed row `assess`. Match the head of it.
    """
    if "is restricted after" in detail:
        return "narrow"
    if "needs review" in detail:
        return "assess"
    return "-"


# --- authorization triples -------------------------------------------------------------------


def _three_way(report: EquivalenceReport) -> str:
    lines = ["  3-way, per source (escalated = the gate sent it to review):"]
    lines.append(f"    {'source':<12} {'n':>3} {'allowed':>8} {'escalated':>10} {'refused':>8}")
    for source in _SOURCES:
        rows = report.by_source(source)
        lines.append(
            f"    {source.value:<12} {len(rows):>3} {sum(o.executed for o in rows):>8} "
            f"{sum(o.escalated for o in rows):>10} {sum(o.refused for o in rows):>8}"
        )
    return "\n".join(lines)


def _per_action(report: EquivalenceReport) -> str:
    lines = ["  per action type — escalated / n, by source:"]
    lines.append(f"    {'action':<14} {'USER':>10} {'LEGIT_TOOL':>12} {'UNTRUSTED':>12}")
    for action in sorted({o.action for o in report.outcomes}):
        cells = []
        for source in _SOURCES:
            rows = [o for o in report.by_source(source) if o.action == action]
            cells.append(f"{sum(o.escalated for o in rows)}/{len(rows)}")
        lines.append(f"    {action:<14} {cells[0]:>10} {cells[1]:>12} {cells[2]:>12}")
    return "\n".join(lines)


def _fp(report: EquivalenceReport) -> str:
    fp = report.false_positive_rate()
    return (
        "  false-positive rate on LEGITIMATE flows:\n"
        f"    USER-sourced       : {fp['fp_user']:.3f}  (n={int(fp['n_user'])})\n"
        f"    LEGIT_TOOL-sourced : {fp['fp_legit_tool']:.3f}  (n={int(fp['n_legit_tool'])})\n"
        f"    pooled legit       : {fp['fp_pooled_legit']:.3f}  (labelled; heterogeneous rows)\n"
        f"    UNTRUSTED (true +) : {fp['tp_untrusted']:.3f}  (n={int(fp['n_untrusted'])})"
    )


def _per_row_triples(report: EquivalenceReport) -> str:
    lines = ["  per row (triple · source · requested_by · mechanism · outcome under auto-deny):"]
    for o in report.outcomes:
        mech = "narrow" if o.narrowed else ("assess" if o.assessed else "-")
        lines.append(
            f"    {o.triple_id:<38} {o.source.value:<11} {o.action:<12} by={o.requested_by:<8} "
            f"tainted={str(o.tainted):<5} esc={str(o.escalated):<5} {mech:<7} "
            f"{'REFUSED' if o.refused else 'ran'}"
        )
    return "\n".join(lines)


def _triples_section(mode: str) -> str:
    unattended = run_equivalence(default_triples(), approve=None, authority=mode)
    approved = run_equivalence(default_triples(), approve=allow(ApprovalLedger()), authority=mode)
    return "\n".join(
        [
            _three_way(unattended),
            _per_action(unattended),
            _fp(unattended),
            _per_row_triples(unattended),
            "  under an approver that says yes (proves escalation, not hard block):",
            "\n".join("  " + line for line in _three_way(approved).splitlines()[1:]),
        ]
    )


# --- injection corpus ----------------------------------------------------------------------------


def _injection_table(attacks: RedTeamReport, benign: BenignReport) -> str:
    a, b = attacks.summary(), benign.summary()
    lines = [
        f"  attacks: block_rate={a['block_rate']:.3f}  asr={a['attack_success_rate']:.3f}  "
        f"asr_exfil={a.get('asr_exfil', 0.0):.3f}  asr_backdoor={a.get('asr_backdoor', 0.0):.3f}  "
        f"asr_destructive={a.get('asr_destructive', 0.0):.3f}  "
        f"asr_self_modify={a.get('asr_self_modify', 0.0):.3f}",
        f"  benign : over_block={b['over_block_rate']:.3f}  "
        f"workspace={b.get('over_block_workspace', 0.0):.3f}  "
        f"fetch={b.get('over_block_fetch', 0.0):.3f}  n={int(b['tasks'])}",
        "  per attack (id · category · requested_by · mechanism · verdict):",
    ]
    for o in attacks.outcomes:
        lines.append(
            f"    {o.id:<26} {o.category:<12} by={o.requested_by:<8} {_mechanism(o.detail):<7} "
            f"{'BLOCKED' if o.blocked else 'EXECUTED'}"
        )
    lines.append("  per legitimate row (id · source · requested_by · mechanism · verdict):")
    for bo in benign.outcomes:
        lines.append(
            f"    {bo.id:<42} {bo.source:<10} by={bo.requested_by:<8} "
            f"{_mechanism(bo.detail):<7} {'REFUSED' if bo.refused else 'ran'}"
        )
    return "\n".join(lines)


def _injection(mode: str, *, user_requested: bool) -> tuple[RedTeamReport, BenignReport]:
    attacks = run_redteam(
        default_attacks(), defended=True, authority=mode, user_requested=user_requested
    )
    benign = run_benign(
        default_benign(), defended=True, authority=mode, user_requested=user_requested
    )
    return attacks, benign


def _changes(
    label: str, before: RedTeamReport, after: RedTeamReport
) -> str:
    """Every attack row whose verdict differs between the two modes, with the mechanism."""
    lines = [f"  {label}:"]
    changed = 0
    for x, y in zip(before.outcomes, after.outcomes, strict=True):
        assert x.id == y.id
        if x.blocked != y.blocked:
            changed += 1
            lines.append(
                f"    {x.id:<26} {x.category:<12} provenance={'BLOCKED' if x.blocked else 'EXECUTED'}"
                f" ({_mechanism(x.detail)})  authority={'BLOCKED' if y.blocked else 'EXECUTED'}"
                f" ({_mechanism(y.detail)})"
            )
    lines.append(
        f"    {changed} of {len(before.outcomes)} attack rows changed verdict; "
        f"slipped under authority: {sum(x.blocked and not y.blocked for x, y in zip(before.outcomes, after.outcomes, strict=True))}"
    )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--tag", default="2026-09-08-authority")
    args = ap.parse_args()
    triples_out: list[str] = []
    injection_out: list[str] = []

    def section(bucket: list[str], title: str, text: str) -> None:
        block = f"## {title}\n{text}\n"
        bucket.append(block)
        print(block)

    n = len(default_triples())
    section(
        triples_out,
        "corpus",
        f"  {n} rows = {n // 3} triples x 3 sources; the same LedgeredTool(narrow_on_taint=True) "
        "in both modes; approve=None unless stated",
    )
    for mode in MODES:
        section(triples_out, f"authorization triples — mode={mode}", _triples_section(mode))

    section(
        injection_out,
        "corpus",
        f"  {len(default_attacks())} attacks, {len(default_benign())} legitimate rows; "
        "approve=None (unattended); the same LedgeredTool(narrow_on_taint=True) in both modes",
    )
    standard: dict[str, RedTeamReport] = {}
    asked: dict[str, RedTeamReport] = {}
    for mode in MODES:
        attacks, benign = _injection(mode, user_requested=False)
        standard[mode] = attacks
        section(injection_out, f"injection, standard rows — mode={mode}", _injection_table(attacks, benign))
    for mode in MODES:
        attacks, benign = _injection(mode, user_requested=True)
        asked[mode] = attacks
        section(
            injection_out,
            f"injection, the user asked to summarise the page — mode={mode}",
            _injection_table(attacks, benign),
        )
    section(
        injection_out,
        "attack rows whose verdict changed between the modes",
        _changes("standard rows", standard["provenance"], standard["authority"])
        + "\n"
        + _changes("the user asked for the page", asked["provenance"], asked["authority"]),
    )

    if args.out_dir:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{args.tag}-authorization.txt").write_text("\n".join(triples_out), encoding="utf-8")
        (out_dir / f"{args.tag}-injection.txt").write_text("\n".join(injection_out), encoding="utf-8")
        print(f"  written {out_dir / (args.tag + '-authorization.txt')}")
        print(f"  written {out_dir / (args.tag + '-injection.txt')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
