"""Every decision point declared once — what it asks, what it may do, and the bench that justifies it.

Study 22 (`bench/PLAN-study22-system-one.md`) turned four measured failures into invariants; two of
them are enforced here by construction rather than by review:

* **I1 — a decision may escalate, never de-escalate.** :class:`Escalation` has only upward members:
  raise a REVIEW card, nudge, trigger a verification, move to a bigger model, annotate. There is no
  ``STOP``, ``SKIP``, ``ACCEPT`` or ``ANSWER`` to return, because the one time a decision could say
  "stop here" (the B4 router, #537) it cost −0.087 / −0.194 / −0.307 oracle score, worse the stronger
  the executor. A surface that wants a de-escalating decision has to change this type, in a diff a
  reviewer reads.
* **I8 — shadow first, bench per surface, fail toward scrutiny.** A spec names the bench file that
  measured it (a test holds that the file exists), starts in :attr:`Mode.SHADOW` (record, never act),
  and says what a halt or an unreadable answer turns into — an escalation, or explicitly nothing
  (``on_no_signal=None``, which the governance band uses because its rules and ledger already decided
  and the band only adds scrutiny on top).

The registry is a plain dict filled at import by the modules that own a decision; :func:`register`
refuses a second spec under the same name so two surfaces cannot disagree about one decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from chimera.decisions.contract import Question


class Escalation(StrEnum):
    """What a decision may cause. Every member adds scrutiny or information; none removes any."""

    REVIEW = "review"
    """A human sees the action before it runs."""
    NUDGE = "nudge"
    """A hint the agent may ignore, injected into its context."""
    VERIFY = "verify"
    """An extra verification pass runs."""
    ESCALATE_MODEL = "escalate_model"
    """The step is retried on a stronger model."""
    ANNOTATE = "annotate"
    """The record carries the answer; nothing else changes."""


class Mode(StrEnum):
    SHADOW = "shadow"
    """Ask and record; the surface ignores the answer."""
    ENFORCE = "enforce"
    """The surface acts on the answer — only after the spec's bench passed its gate."""


@dataclass(frozen=True)
class DecisionSpec:
    """One decision point: the questions it asks, the escalation it may cause, and its evidence."""

    name: str
    """The calibration key — ``governance.danger``. Maps are fitted per name."""
    questions: tuple[Question, ...]
    """Atomic questions (I6: decompose, combine in code). Each is read in isolation."""
    escalation: Escalation
    """What crossing the threshold does. Upward by type."""
    bench: str
    """Repo-relative path to the bench that measured this decision — a file that must exist."""
    threshold: float | None = None
    """The calibrated ``p`` at or above which the escalation fires; ``None`` for a spec whose
    surface reads the answer through its own policy (the band's two-threshold hysteresis)."""
    mode: Mode = Mode.SHADOW
    on_no_signal: Escalation | None = None
    """What a halt, an unreadable answer or an uncalibrated ``p`` becomes. ``None`` means "the
    surface's other layers stand" — never "pass"."""
    description: str = ""
    surfaces: tuple[str, ...] = field(default_factory=tuple)
    """Repo-relative modules that consume this spec — the reader's map from decision to code."""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("a decision spec needs a name")
        if not self.questions:
            raise ValueError(f"{self.name}: a decision asks at least one question")
        if not isinstance(self.escalation, Escalation):
            raise TypeError(f"{self.name}: escalation must be an Escalation, got {self.escalation!r}")
        if self.on_no_signal is not None and not isinstance(self.on_no_signal, Escalation):
            raise TypeError(f"{self.name}: on_no_signal must be an Escalation or None")
        if self.threshold is not None and not 0.0 < self.threshold < 1.0:
            raise ValueError(f"{self.name}: a threshold is a probability strictly between 0 and 1")
        if not self.bench.strip():
            raise ValueError(f"{self.name}: a decision without a bench has no evidence (study 22, I8)")
        keys = [q.key for q in self.questions]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{self.name}: question keys repeat: {keys}")

    def fires(self, p: float | None, *, calibrated: bool) -> Escalation | None:
        """The escalation this answer causes, or ``None``. Only a calibrated ``p`` crosses a
        threshold (I5); no number, or an uncalibrated one, is no signal → ``on_no_signal``."""
        if p is None or not calibrated:
            return self.on_no_signal
        if self.threshold is not None and p >= self.threshold:
            return self.escalation
        return None


REGISTRY: dict[str, DecisionSpec] = {}


def register(spec: DecisionSpec) -> DecisionSpec:
    """Add ``spec`` to the registry. The same object twice is a no-op (a module re-imported); a
    different spec under a registered name is refused."""
    existing = REGISTRY.get(spec.name)
    if existing is not None and existing != spec:
        raise ValueError(f"decision {spec.name!r} is already registered with a different spec")
    REGISTRY[spec.name] = spec
    return spec
