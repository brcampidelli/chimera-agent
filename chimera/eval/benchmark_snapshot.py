"""Build a plain-dict benchmark snapshot + write the shipped JSON — the app's REAL performance numbers.

Unlike the maturity scorecard (coverage = test-file presence), this carries the project's **recorded
benchmark results**, honestly framed. Two blocks, both true, kept together on purpose:

- **internal_lift** — READ live from ``bench/local_lift/results/paired.json`` (so re-running the
  local_lift suite + regenerating this snapshot auto-updates it). The weak-model lift: a cheap model +
  Chimera's retry loop vs the cheap model alone, on a pre-registered suite. At n=100 the lift IS
  significant (the paired 95% CI excludes 0) and comes entirely from tasks the loop RECOVERED with
  zero regressions — but it is one model on small self-contained tasks, and the suite label makes
  clear it is NOT SWE-bench / Terminal-Bench.
- **external** — a recorded, cited external result (Terminal-Bench). Carried as a constant with a
  ``source`` pointing at ``bench/terminal_bench/RESULTS.md`` and the exact published numbers. It is
  HUMBLING (the scaffold did not lift an already-competent model) and also not significant at N=40.
  Published anyway — the integrity story is exactly this pairing.

HONESTY BY CONSTRUCTION: every number here traces to a committed results file. The ``significant``
flag and the ``n`` / ``ci`` fields travel WITH each number so no surface can show a lift without the
caveats that qualify it. Nothing is rounded to flatter; nothing is re-rolled for significance.

Running ``python -m chimera.eval.benchmark_snapshot`` writes ``chimera/_benchmark_snapshot.json`` (the
shipped data — the ``bench/`` dir is not packaged in the wheel, so the app reads this snapshot). The
write is byte-stable (sorted keys, 2-space indent, trailing newline) so regenerating gives a clean diff.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera import __version__

# The external Terminal-Bench block is a recorded, cited result — not something local_lift regenerates.
# It is carried as a constant with its source path + the exact published numbers (see the RESULTS.md).
# It is the HUMBLING half of the honesty story and travels with n / ci / significant so no surface can
# drop the caveat.
_TERMINAL_BENCH: dict[str, Any] = {
    "benchmark": "Terminal-Bench (terminal-bench-core 0.1.1)",
    "model": "openrouter/deepseek/deepseek-chat-v3.1",
    "n": 40,
    "baseline_rate": 0.075,  # 3/40, bare model 1-shot
    "treatment_rate": 0.025,  # 1/40, + Chimera scaffold (repo-map + ledger + checklist) 1-shot
    "delta": -0.05,  # paired delta, pp as a fraction
    "ci": [-0.05, 0.016],  # 95% paired CI — includes 0
    "significant": False,
    "source": "bench/terminal_bench/RESULTS.md",
    "note": (
        "Single-attempt, N=40, same model both arms. The scaffold did NOT lift here — deepseek-v3.1 is "
        "already competent, not the weak 'goldilocks' regime where scaffolding helps; both arms sit near "
        "the floor and the difference is not significant (CI includes 0). Published as measured; the "
        "point estimate is dominated by run-to-run variance at this floor."
    ),
}


def _repo_root() -> Path:
    """The repo root, resolved from this file: ``chimera/eval/benchmark_snapshot.py`` → parents[2]."""
    return Path(__file__).resolve().parents[2]


def _default_paired_path() -> Path:
    """The committed local_lift paired result the internal-lift block is read from."""
    return _repo_root() / "bench" / "local_lift" / "results" / "paired.json"


def _internal_lift(paired_path: Path) -> dict[str, Any]:
    """Flatten ``bench/local_lift/results/paired.json`` into the internal-lift block.

    Reads the real file so a re-run of local_lift + a regenerate auto-updates the shipped number. The
    ``significant`` flag and the ``n`` / ``ci`` fields are carried through verbatim so the caveat can't
    be dropped downstream. The ``note`` is prose ABOUT the current recorded run — if a re-run changes
    the verdict (e.g. significance flips), update it here, never by hand-editing the shipped JSON.
    """
    data = json.loads(paired_path.read_text(encoding="utf-8"))
    summary = data["summary"]
    discordant = summary["discordant"]
    return {
        "suite": "internal Docker-free suite (local_lift), pytest-graded — NOT SWE-bench/Terminal-Bench",
        "model": data["model"],
        "n": summary["n"],
        "baseline_rate": summary["baseline_rate"],
        "treatment_rate": summary["treatment_rate"],
        "delta": summary["delta"],
        "ci": list(summary["diff_ci"]),
        "significant": summary["significant"],
        "discordant": {
            "treatment_only": discordant["treatment_only"],
            "baseline_only": discordant["baseline_only"],
        },
        "source": "bench/local_lift/results/paired.json",
        "note": (
            "A cheap weak model + Chimera's retry loop vs the cheap model alone, on a pre-registered "
            "n=100 suite (design + tasks committed before any model call). SIGNIFICANT: the paired 95% "
            "CI excludes zero. The lift is 6 tasks the loop RECOVERED (raw fail → verified pass) with 0 "
            "regressions. Absolute rates are low (9%/15%) because 85 of the 100 tasks are hard enough to "
            "fail both arms — a deliberate floor so the loop has headroom; the lift is real where the "
            "model has a chance. One model, one seed/task, small self-contained Python tasks — NOT "
            "SWE-bench, does not generalise to real repos. One run, no re-roll."
        ),
    }


def build_snapshot(paired_path: Path | None = None) -> dict[str, Any]:
    """Build the benchmark snapshot dict: the live internal-lift block + the recorded external block.

    ``paired_path`` defaults to the committed ``bench/local_lift/results/paired.json`` and exists only
    so tests can point the reader at a temp file.
    """
    path = paired_path if paired_path is not None else _default_paired_path()
    return {
        "internal_lift": _internal_lift(path),
        "external": [dict(_TERMINAL_BENCH)],
        "generated_for": __version__,
    }


def snapshot_path() -> Path:
    """The shipped data location: ``chimera/_benchmark_snapshot.json`` (inside the package)."""
    return Path(__file__).resolve().parent.parent / "_benchmark_snapshot.json"


def main() -> None:
    """Write the shipped snapshot from the committed paired result, byte-stably."""
    snapshot = build_snapshot()
    out = snapshot_path()
    # Force LF (newline="\n") so the file is byte-identical on Windows and Linux/CI (see maturity_snapshot).
    out.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    lift = snapshot["internal_lift"]
    print(
        f"wrote {out} (internal lift {lift['baseline_rate']:.0%} -> {lift['treatment_rate']:.0%}, "
        f"n={lift['n']}, significant={lift['significant']})"
    )


if __name__ == "__main__":
    main()
