"""The arms of `PREREGISTRATION_attended.md`, run against the shipped harness. US$ 0 — stub tools.

    python bench/injection/run_attended.py [--out bench/injection/results/<date>-attended.txt]

Every arm uses `chimera.eval.injection` as shipped — the same seven attacks, the same eight
legitimate rows, the same `LedgeredTool` wrapper — and differs only in the approver handed to it,
which is the person each arm assumes:

* **control** — no approver: what an unattended run gets today.
* **1a nobody answers** — `ask_elsewhere` with the wait forced to zero: a question is written,
  announced, and refused. Attack AND benign corpora.
* **1b the person approves what they asked for** — `allow` on the benign corpus ONLY; the attacks
  still get 1a's approver. Handing the yes to the attacks would model a user who approves whatever
  an injected page asks for, which measures nothing about the defence.
* **1c the person refuses everything** — `deny` on both. The floor.

`questions_asked` is counted at the moment of announcement, and the question file is checked to
exist right then — `ask_durably` cleans a timed-out question up, so counting files afterwards would
count nothing and read as zero. The instrument is the announcement; the file check is what makes it
"a question was written" rather than "a callback fired".
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from chimera.eval.injection import (  # noqa: E402
    BenignReport,
    RedTeamReport,
    default_attacks,
    default_benign,
    run_benign,
    run_redteam,
)
from chimera.governance.approval import ApprovalLedger, allow, ask_elsewhere, deny  # noqa: E402


def _asker(home: Path, asked: list[Any]) -> Any:
    """1a: a durable ask that nobody answers. Records each question as it is written."""

    def on_asked(question: Any) -> None:
        ask_file = home / "approvals" / f"{question.id}.ask.json"
        assert ask_file.exists(), "announced a question that is not on disk"
        asked.append(question)

    return ask_elsewhere(home, on_asked=on_asked, wait_seconds=0.0)


def _table(attacks: RedTeamReport, benign: BenignReport, asked: int | None) -> str:
    a, b = attacks.summary(), benign.summary()
    lines = [
        f"  attacks: block_rate={a['block_rate']:.3f}  asr={a['attack_success_rate']:.3f}  "
        f"asr_exfil={a.get('asr_exfil', 0.0):.3f}  asr_backdoor={a.get('asr_backdoor', 0.0):.3f}  "
        f"asr_destructive={a.get('asr_destructive', 0.0):.3f}  asr_self_modify={a.get('asr_self_modify', 0.0):.3f}",
        f"  benign : over_block={b['over_block_rate']:.3f}  workspace={b.get('over_block_workspace', 0.0):.3f}  "
        f"fetch={b.get('over_block_fetch', 0.0):.3f}  n={int(b['tasks'])}"
        + (f"  questions_asked={asked}" if asked is not None else ""),
        "  per attack:",
    ]
    for o in attacks.outcomes:
        lines.append(f"    {o.id:<26} {o.category:<12} {'BLOCKED' if o.blocked else 'EXECUTED'}")
    lines.append("  per legitimate row:")
    for o in benign.outcomes:
        lines.append(f"    {o.id:<42} {o.source:<10} {'REFUSED' if o.refused else 'ran'}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    out: list[str] = []

    def section(title: str, text: str) -> None:
        out.append(f"## {title}\n{text}\n")
        print(f"## {title}\n{text}\n")

    # control — no approver anywhere
    section(
        "control: no approver (shipped, unattended)",
        _table(run_redteam(default_attacks(), defended=True), run_benign(default_benign(), defended=True), None),
    )

    # 1a — nobody answers
    with tempfile.TemporaryDirectory(prefix="attended-1a-") as tmp:
        home = Path(tmp)
        asked_a: list[Any] = []
        asked_b: list[Any] = []
        attacks = run_redteam(default_attacks(), defended=True, approve=_asker(home, asked_a))
        benign = run_benign(default_benign(), defended=True, approve=_asker(home, asked_b))
        section(
            "1a: nobody answers (durable ask, wait 0)",
            _table(attacks, benign, len(asked_b))
            + f"\n  questions asked on the ATTACK corpus: {len(asked_a)} (every narrowed attack is a question too)"
            + f"\n  questions left on disk afterwards: {len(list((home / 'approvals').glob('*.ask.json')))}",
        )

    # 1b — the person approves the work they asked for (benign only)
    with tempfile.TemporaryDirectory(prefix="attended-1b-") as tmp:
        home = Path(tmp)
        asked_a2: list[Any] = []
        book = ApprovalLedger()
        attacks = run_redteam(default_attacks(), defended=True, approve=_asker(home, asked_a2))
        benign = run_benign(default_benign(), defended=True, approve=allow(book))
        section(
            "1b: the person approves the work they asked for (benign only; attacks still unanswered)",
            _table(attacks, benign, len(book.granted) + len(book.refused)),
        )

    # 1c — the person refuses everything
    book_c = ApprovalLedger()
    section(
        "1c: the person refuses everything",
        _table(
            run_redteam(default_attacks(), defended=True, approve=deny(book_c)),
            run_benign(default_benign(), defended=True, approve=deny(book_c)),
            None,
        ),
    )

    if args.out:
        Path(args.out).write_text("\n".join(out), encoding="utf-8")
        print(f"  written {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
