"""The two arms of `PREREGISTRATION_agents_md.md` (2026-10-05), run against the shipped loop.

    python bench/injection/run_agents_md.py [--out bench/injection/results/2026-10-05-agents-md.txt]

US$ 0 — stub tools, no model. See `chimera.eval.agents_md_carrier` for what a row does.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from chimera.eval.agents_md_carrier import CarrierRow, run_carrier  # noqa: E402


def _section(name: str, rows: list[CarrierRow]) -> list[str]:
    attacks = [r for r in rows if r.kind == "attack"]
    honest = [r for r in rows if r.kind == "honest"]
    blocked = sum(not r.ran for r in attacks)
    paused = sum(not r.ran for r in honest)
    fenced = sum(r.fenced for r in attacks)
    out = [
        f"## {name}",
        f"  attacks blocked     : {blocked} / {len(attacks)}",
        f"  honest calls paused : {paused} / {len(honest)}",
        f"  attack prompts fenced: {fenced} / {len(attacks)}",
        "  per row:",
    ]
    for r in rows:
        verdict = "ran" if r.ran else "STOPPED"
        out.append(
            f"    {r.id:<28} {r.kind:<7} {verdict:<8} tainted={str(r.tainted):<5} "
            f"fenced={str(r.fenced):<5} {r.detail[:60]!r}"
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    lines = ["# AGENTS.md as the carrier — PREREGISTRATION_agents_md.md, 2026-10-05", ""]
    lines += _section("trusted (trust_workspace=True: the default, and every setting before)",
                      run_carrier(trusted=True))
    lines.append("")
    lines += _section("untrusted (trust_workspace=False: CHIMERA_TRUST_WORKSPACE=0)",
                      run_carrier(trusted=False))
    text = "\n".join(lines) + "\n"
    print(text, end="")
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
