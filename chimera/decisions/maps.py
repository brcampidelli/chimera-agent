"""The calibration maps this package ships — each the output of `bench/jev_decisions/fit_map.py` on
the rows it names, held equal to a refit by the tests, so a constant here is never a number without
a procedure behind it.

One map today, for the one decision that has a labelled corpus: the governance question on the
local backend. What it does to the raw ``p`` of ``qwen3:4b`` (read off the label token, decision-
first): 0.50 → 0.07, 0.90 → 0.26, 0.99 → 0.66, 0.999 → 0.91. That is the shape the bench found —
the small model says "dangerous" at 0.99 for actions the labels call benign, so a raw 0.9 is a
coin-flip at best — and the reason the raw number is never shown as a probability.

Not shipped, and why: a map for the hosted verbalized backend on this decision (the bench found it
calibrated raw, ECE 0.051 at a floor of ~0.05, and every map fitted on it made it worse); a map for
the review decision on any backend (neither arm reached the pre-registered operating point on
aacr-bench, so there is no decision to calibrate yet); a map for the vendor backend (it would be
fitted on a run whose model version the vendor alone controls — a deployment that turns that backend
on fits its own from its own rows, `CalibrationMaps.load`).
"""

from __future__ import annotations

from chimera.decisions.calibration import PlattMap

SHIPPED_MAPS: tuple[PlattMap, ...] = (
    PlattMap(
        id="governance.danger/local_logprob/qwen3:4b/88439bc0d2bd/2026-09-19",
        decision="governance.danger",
        backend="local_logprob",
        model="qwen3:4b",
        prompt_hash="88439bc0d2bd",
        a=0.720726414152165,
        b=-2.641233966873504,
        n=55,
        positives=24,
        fitted_at="2026-09-19",
        source="bench/jev_decisions/results/2026-09-19-local-L.jsonl — arm L, rep 0, no wrapper, easy + ambiguous",
        note=(
            "leave-one-family-out on the same rows (RESULTS.md §7b): Brier 0.268 → 0.135, ECE 0.299 → 0.085 "
            "(floor 0.08), catch 20/24 at FR 6/31 at τ = 0.5 — the hosted judge's operating point at US$ 0"
        ),
        # `/api/tags` on the machine that ran the rows, 2026-09-19: digest 359d7dd4bcda…, 4.0B, Q4_K_M. The
        # rows themselves did not record it (study 21 is where that gap was found).
        resolved_model="qwen3:4b@Q4_K_M",
    ),
)
