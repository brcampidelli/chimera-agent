"""A typed decision, asked of a backend, calibrated on our own labels, with a receipt.

The interface study 20 said transfers from the vendor and the vendor does not: a question with a
closed set of answers (:class:`Noul` — P(yes); :class:`Choice` — a distribution over options;
:class:`Score` — ordered levels with an expectation) put to a *backend* that returns a distribution,
passed through a *calibration map* fitted on a labelled set of **this** decision, and returned as an
:class:`Answer` whose receipt says which backend answered, whether a map existed, and how much of the
model's mass was on the options at all.

Three backends, each the instrument the bench measured (`bench/jev_decisions/RESULTS.md`), byte for
byte where a map depends on it:

* :class:`~chimera.decisions.local.LocalLogprobBackend` — a small instruct model on this machine
  through Ollama, decision-first (no reasoning), the answer constrained to the options, the
  probability read off the label token's log-probabilities renormalized over the options. On the
  governance corpus `qwen3:4b` ranks like the hosted judge and the vendor (AUROC 0.871 / 0.874 /
  0.903 on the ambiguous slice) and is saturated (Brier 0.268, ECE 0.299); through a Platt map
  fitted leave-one-family-out it reaches Brier 0.135, ECE 0.085 — at the floor — and the hosted
  judge's operating point, at US$ 0 and 0.75 s a call. **The default.**
* :class:`~chimera.decisions.hosted.HostedVerbalizedBackend` — the configured chat model asked for
  a verbalized probability with the over-confidence advisory, reasoning off, a wide budget and one
  re-ask on an empty answer. AUROC 0.886, Brier 0.097, ECE 0.051 on the same slice; 4–5 s a call.
* :class:`~chimera.decisions.openrouter.OpenRouterDecisionsBackend` — OpenRouter's Decisions API
  (a typed-decision model behind it, pinned), 0.34 s a call, deterministic, 5–8× fewer framing flips
  than either model backend — and over-confident in the middle of its scale (p̄ 0.60 → 27% correct),
  so it needs the map like the others. Optional, behind :attr:`Settings.decision_backend`; fails
  closed per call (this call has no answer, the run continues) and is never the only layer.

**The map is part of the contract, not an afterthought.** Measured on 2026-09-19 on the one corpus
with human labels (aacr-bench, 919 rows): the vendor's own calibration does not transfer to a
decision it was not trained on (ECE 0.405 against a floor of 0.035), and neither does the verbalized
model's (0.544). A map fitted on one decision, one backend, one model and one wording of the question
applies to exactly that — :class:`~chimera.decisions.calibration.CalibrationMaps` keys on all four —
and an answer says ``calibrated=False`` rather than pretending. No surface in this package wires a
:class:`Decider` yet; the kernel's REVIEW band, the strong verifier, the voice router are each their
own change with their own measurement, on top of this one.
"""

from chimera.decisions.calibration import CalibrationMaps, PlattMap, fit_platt, prompt_hash
from chimera.decisions.contract import (
    Answer,
    Choice,
    Decider,
    DecisionBackend,
    Noul,
    Question,
    Reading,
    Score,
    as_choice,
)

__all__ = [
    "Answer",
    "CalibrationMaps",
    "Choice",
    "DecisionBackend",
    "Decider",
    "Noul",
    "PlattMap",
    "Question",
    "Reading",
    "Score",
    "as_choice",
    "fit_platt",
    "prompt_hash",
]
