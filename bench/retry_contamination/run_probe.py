"""Item 1+7, Step 0 — is a retry an independent draw? A probe over this install's own receipts.

arXiv 2605.08563: retries of a coding agent are not independent — the IID model overestimated
pass@3 by 17.4 pp. The plan's hand count over this desktop's ``runs.jsonl`` pointed the same way
(2nd attempt after a failed 1st: 4/21; 3rd after a failed 2nd: 0/11, where independence at ~0.19
expects ~2). That count is the PREDICTION this probe checks, not the answer — see RESULTS.md, whose
prediction section was written before this ran.

Everything statistical lives in :mod:`chimera.eval.retries` (typed, under the package's mypy gate,
in the style of :mod:`chimera.eval.replicated`); this file only finds the receipts and prints. It
prints counts and money, never task text: a public repository is a training corpus, and the
receipts belong to one user's projects.

What it cannot show is stated in the output and in RESULTS.md: ``max_attempts`` is not on the
receipt, so pass@k eligibility is inferred and bracketed; the receipts are one install, one user's
tasks, several models, over eleven days — a population, not a benchmark.

Usage:  uv run python bench/retry_contamination/run_probe.py --receipts PATH
        CHIMERA_RECEIPTS=PATH uv run python bench/retry_contamination/run_probe.py
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from chimera.eval.retries import format_report, read_receipts  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--receipts",
        default=os.environ.get("CHIMERA_RECEIPTS", ""),
        help="path to runs.jsonl (or set CHIMERA_RECEIPTS); never copied into the repository",
    )
    ap.add_argument("--alpha", type=float, default=0.05, help="tail threshold for the flag")
    ap.add_argument("--max-k", type=int, default=3, help="deepest pass@k row to print")
    args = ap.parse_args()
    if not args.receipts:
        print("!! no receipts: pass --receipts PATH or set CHIMERA_RECEIPTS")
        return 2
    path = pathlib.Path(args.receipts)
    if not path.exists():
        print(f"!! receipts not found: {path}")
        return 2
    file = read_receipts(path)
    if not file.runs:
        print(
            f"!! nothing readable in {path}: {file.unreadable} unreadable, {file.malformed} "
            "malformed — the probe measured nothing. Do not interpret."
        )
        return 2
    print(format_report(file, alpha=args.alpha, max_k=args.max_k))
    print()
    print(
        "LIMIT: one install, one user's tasks, mixed models. The conditional rows are the honest "
        "reading; pass@k is bracketed because the receipt does not record max_attempts."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
