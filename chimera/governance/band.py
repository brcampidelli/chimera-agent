"""The REVIEW band: a calibrated probability from a typed decision, turned into a verdict — or a prior.

Study 20 §3 C1, built after the bench said what the number is worth (`bench/jev_decisions/RESULTS.md`)
and study 21 said which number to read. Where the lexical rules matched nothing, the kernel asks the
configured decider the governance question (`chimera/decisions/governance.py`: "is this shell action
dangerous?", P(BLOCK or REVIEW)) and reads the answer against **two thresholds**:

* ``p ≥ review_at`` → **REVIEW**: a person sees the action first. That is the only verdict this band
  ever returns — a benign action is never hard-blocked by an inference (the kernel's invariant), and
  REVIEW is the decision the approval card exists for.
* ``p < allow_below`` → ALLOW, confidently: the kernel's default with the number on the audit line.
* between → ALLOW as **a prior, not a verdict**: the default applies, the audit line carries ``p`` and
  ``band: uncertain``, and whoever reads the log can see where the model was unsure. The three
  outcomes are the three bands the ecosystem converged on (study 21 §2.5), with the edges read off a
  ROC instead of picked by hand.

**The thresholds, and where they come from.** On the calibrated local arm (qwen3:4b decision-first,
through the shipped Platt map; leave-one-family-out on the 55 governance items, 24 attacks / 31
benign twins): at τ = 0.50 catch 20/24 with 6/31 benign actions stopping for a card — the hosted
judge's own operating point, at US$ 0 and 0.3 s; at 0.30 catch 23/24 with 9/31 stopping; at 0.70
catch 14/24 with 4/31. So ``review_at = 0.50`` (the judge's point) and ``allow_below = 0.30`` (below it
the arm misses at most one attack in 24; between 0.30 and 0.50 sit the items the arm cannot place —
``docker system prune``, ``git reset --hard`` on the benign side, a base64-piped script on the
attack side). A deployment that tolerates a different false-refusal rate moves both in Settings, and
a deployment that refits the map on its own rows reads its own ROC first.

**Which number.** The Noul-shaped probability of the event — never a raw Choice mass against a
threshold (on our own rows the Choice mass sits +0.11 above the Noul and calibrates worse, ECE 0.221
against 0.120), never the vendor's ``confidence`` (an unpublished statistic that moves with the
number of options). The band consumes ``Answer.p`` **only when ``calibrated`` is true**: an
uncalibrated number — no map for this instrument, or a map fitted on another build — is recorded
as a prior and thresholds nothing (`band: uncalibrated`). A halt (server down, model not pulled)
records itself and the kernel goes on with its other layers (`band: halt`).

**Hysteresis.** The local arm at temperature 0 still moves by up to 0.05 raw between two runs of the
same item (one flip in 55 at τ = 0.5, `bench/jev_decisions` L arm, rep 0 against rep 1), and the map
stretches that near the middle of the scale. So an action that entered REVIEW leaves it only when its
``p`` falls below ``exit_at`` (0.40, halfway down the band), keyed on the exact action text, in a
bounded memory — a retry of the same command does not flip verdicts on route noise. The memory is
per kernel, so it lives as long as the run that built it.

**What the decider sees.** The action alone — the instrument the map was fitted on. Not the task
context, not the tool output, not the wrapper sentence a request may carry: `bench/perturbation_floor`
§8 measured what one unverifiable sentence does to a judge, and the vendor's own failure-mode page
says the model "does not treat data as hostile by default". The rules and the ledger never see the
wrapper either; that is the point of them.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from chimera.decisions.contract import Answer, Decider, Question
from chimera.decisions.governance import DANGER, DECISION
from chimera.governance.policy import Decision, Verdict
from chimera.telemetry import get_logger

_log = get_logger("governance.band")

RULE = "decision_band"
REVIEW_AT = 0.50
ALLOW_BELOW = 0.30
EXIT_AT = 0.40


@dataclass(frozen=True)
class Band:
    """The two thresholds and the hysteresis exit, validated once."""

    review_at: float = REVIEW_AT
    allow_below: float = ALLOW_BELOW
    exit_at: float = EXIT_AT

    def __post_init__(self) -> None:
        if not 0.0 <= self.allow_below <= self.exit_at <= self.review_at <= 1.0:
            raise ValueError(
                f"a band needs 0 ≤ allow_below ({self.allow_below}) ≤ exit_at ({self.exit_at}) "
                f"≤ review_at ({self.review_at}) ≤ 1"
            )


@dataclass(frozen=True)
class BandReading:
    """What the band made of one action: a verdict when it has one, and the facts for the audit line."""

    verdict: Verdict | None
    band: str
    """``review`` · ``uncertain`` · ``allow`` · ``uncalibrated`` · ``halt`` · ``none`` (no number came)."""
    p: float | None
    """The calibrated probability, when there was one."""
    raw_p: float | None
    answer: Answer

    def audit(self) -> dict[str, Any]:
        """The columns the kernel adds to the audit line. ``confidence`` travels on the verdict
        itself when calibrated; here go the band and, when the number could not be a probability,
        the raw reading — so a log never shows an uncalibrated number in the column a screen reads
        as one."""
        out: dict[str, Any] = {"band": self.band, "decider": self.answer.backend}
        if self.answer.resolved_model:
            out["decider_model"] = self.answer.resolved_model
        if self.band == "uncalibrated" and self.raw_p is not None:
            out["raw_p"] = round(self.raw_p, 4)
        if self.answer.note:
            out["decider_note"] = self.answer.note
        if self.answer.halt:
            out["decider_halt"] = self.answer.halt[:120]
        return out


class DecisionBand:
    """Asks the decider the governance question and reads the answer against the band."""

    def __init__(
        self,
        decider: Decider,
        band: Band | None = None,
        *,
        decision: str = DECISION,
        question: Question = DANGER,
        remember: int = 256,
    ) -> None:
        self.decider = decider
        self.band = band or Band()
        self.decision = decision
        self.question = question
        self._in_review: OrderedDict[str, None] = OrderedDict()
        self._remember = remember

    def read(self, action: str) -> BandReading:
        answer = self.decider.decide(self.decision, action, self.question)
        if answer.halt:
            _log.debug("decision band halted: %s", answer.halt)
            return BandReading(None, "halt", None, None, answer)
        if answer.p is None:
            return BandReading(None, "none", None, None, answer)
        if not answer.calibrated:
            return BandReading(None, "uncalibrated", None, answer.raw_p, answer)
        p = answer.p
        was_in_review = action in self._in_review
        review = p >= self.band.review_at or (was_in_review and p >= self.band.exit_at)
        if review:
            self._remember_review(action)
            reason = (
                f"the decision model put p={p:.2f} on dangerous"
                + (f" (still above the band's exit at {self.band.exit_at:.2f})" if p < self.band.review_at else f" (≥ {self.band.review_at:.2f})")
                + "; a person should approve it before it runs"
            )
            model_id = answer.resolved_model or answer.backend
            return BandReading(
                Verdict(Decision.REVIEW, reason, RULE, confidence=p, band="review", model=model_id),
                "review",
                p,
                answer.raw_p,
                answer,
            )
        self._in_review.pop(action, None)
        band = "allow" if p < self.band.allow_below else "uncertain"
        return BandReading(None, band, p, answer.raw_p, answer)

    def _remember_review(self, action: str) -> None:
        self._in_review[action] = None
        self._in_review.move_to_end(action)
        while len(self._in_review) > self._remember:
            self._in_review.popitem(last=False)


def band_enabled(settings: Any) -> bool:
    """``CHIMERA_GOVERNANCE_BAND=on`` — and only under a governance mode that judges anything."""
    mode = (getattr(settings, "governance_mode", "") or "off").strip().lower()
    wanted = (getattr(settings, "governance_band", "") or "off").strip().lower()
    return mode != "off" and wanted == "on"


def build_band(settings: Any) -> DecisionBand:
    """The band from the settings: the configured decider (`chimera/decisions/factory.py`) and the
    two thresholds. Built once per assembly — the decider holds one client, and the band's memory
    is the run's."""
    from chimera.decisions.factory import build_decider

    return DecisionBand(
        build_decider(settings),
        Band(review_at=float(settings.governance_band_review_at), allow_below=float(settings.governance_band_allow_below)),
    )
