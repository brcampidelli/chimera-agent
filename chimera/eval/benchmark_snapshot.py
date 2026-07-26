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

# SWE-bench Verified — the strongest external result, and the one that survived a replication.
# The OUT-OF-SAMPLE run is what ships here, not the pooled figure: pooling run 2 with run 3 crosses
# significance, but it mixes seen with unseen data and was pre-registered as SECONDARY, so surfacing
# it as the headline would be picking the flattering statistic after the fact. The pooled number is
# named in the note, where its caveat travels with it.
_SWE_BENCH: dict[str, Any] = {
    "benchmark": "SWE-bench Verified (41-instance django easy slice, out-of-sample replication)",
    "model": "openrouter/deepseek/deepseek-chat-v3.1",
    "n": 41,
    "baseline_rate": 0.341,  # 14/41, bare model
    "treatment_rate": 0.439,  # 18/41, + Chimera scaffold at an adequate step budget
    "delta": 0.098,  # paired delta, pp as a fraction
    "ci": [-0.035, 0.167],  # 95% paired CI — includes 0
    "significant": False,
    "source": "bench/swe_bench/RESULTS.md",
    # The three-way decomposition: a fourth run restored the plain-scaffold arm on these same 41
    # instances, so each arm differs from the next by exactly one component. Carried here because a
    # headline delta without it invites "which part actually worked?" — and because the answer
    # contradicted our own registered prediction, which the note says out loud.
    "decomposition": [
        {"arm": "baseline", "resolved": 14, "n": 41, "rate": 0.341, "precision_when_edited": 0.50},
        {"arm": "scaffold", "resolved": 16, "n": 41, "rate": 0.390, "precision_when_edited": 0.59},
        {"arm": "scaffold+diff-gate", "resolved": 18, "n": 41, "rate": 0.439, "precision_when_edited": 0.67},
    ],
    "note": (
        "NOT a SWE-bench Verified score — a deliberately easy, single-repo slice; a real score needs "
        "the full 500. Graded only by the official swebench harness in Docker, never self-reported. "
        "This is the OUT-OF-SAMPLE replication: 41 instances never used by the earlier run, nothing "
        "else changed. The delta is NOT significant on its own (CI includes 0); pooling it with the "
        "earlier 19 gives +11.7% [+0.8%, +16.4%], which IS significant but mixes seen with unseen "
        "data and was pre-registered as secondary. DECOMPOSITION (4th run, middle arm restored on the "
        "same 41): scaffold alone +4.9% over baseline, the diff-gate a further +4.9% — BOTH components "
        "contribute in roughly equal halves, neither significant alone. That CONTRADICTED our own "
        "registered prediction that the scaffold would carry most of it, and withdrew an earlier "
        "reading that the gate was not what produced the gain; the retraction is in RESULTS.md. The "
        "tidy additivity is NOT claimed as a measured 50/50 split — each comparison rests on 5-6 "
        "discordant pairs. All three arms edit at the SAME rate (27-28 patches of 41); what climbs is "
        "precision, 50% -> 59% -> 67%. An earlier run of the same design at a starved step budget "
        "scored an exact 0.0pp and is published unchanged."
    ),
}


def _repo_root() -> Path:
    """The repo root, resolved from this file: ``chimera/eval/benchmark_snapshot.py`` → parents[2]."""
    return Path(__file__).resolve().parents[2]


def _default_paired_path() -> Path:
    """The committed local_lift paired result the internal-lift block is read from.

    Points at ``_reverify_n100/`` — the **canonical** run — not ``results/``. The original n=100 run
    was graded with a test the agent under test could edit (and did), so its numbers are superseded;
    ``results/`` deliberately still holds that original raw data, unedited, because the erratum in
    ``bench/local_lift/RESULTS.md`` cites it — superseding a number is not hiding it. Reading
    ``results/`` here would ship the retracted 9% → 15% figure to the desktop UI instead of the
    re-verified 48% → 71%.
    """
    return _repo_root() / "bench" / "local_lift" / "_reverify_n100" / "paired.json"


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
        # Derived from the file actually read, never hardcoded: the source must point at where the
        # numbers came from. It briefly did not — v0.36.0 shipped the corrected numbers citing the
        # superseded run's path, so anyone checking the citation would have found different figures.
        "source": paired_path.relative_to(_repo_root()).as_posix(),
        "note": (
            "A cheap weak model + Chimera's retry loop vs the cheap model alone, on a pre-registered "
            "n=100 suite (design + tasks committed before any model call). SIGNIFICANT: the paired 95% "
            "CI excludes zero. The lift is 28 tasks the loop RECOVERED (raw fail → verified pass) "
            "against 5 regressions. This is the RE-VERIFIED run: an earlier run of the same suite "
            "(9% → 15%) was graded with a test file the agent under test could edit — and on re-run it "
            "did edit one — so grading was hardened to restore the pristine test, and the lift "
            "replicated LARGER, not smaller. The superseded run is kept unedited beside the erratum in "
            "bench/local_lift/RESULTS.md. One model, one seed/task, small self-contained Python tasks "
            "— NOT SWE-bench, does not generalise to real repos. One run, no re-roll."
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
        # Both recorded external results travel together, on purpose: the one that points our way and
        # the one that does not. Showing only the flattering half would be exactly the selective
        # reporting the benchmarks section exists to avoid.
        "external": [dict(_SWE_BENCH), dict(_TERMINAL_BENCH)],
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
