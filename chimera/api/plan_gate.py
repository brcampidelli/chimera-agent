"""Ask the person about the PLAN, once, before the turn touches anything.

The gate this project already had is per tool call: the model produces a call, the kernel judges
it, and a REVIEW parks the worker thread on a question (``governed_tool.py`` →
``pending.ask_durably``). That gate is a real control and it stays exactly as it is. What it is
*not* is the control people assume it is.

Two human-subject studies, different designs, same conclusion:

* arXiv 2604.04918 (N=48, within-subjects, live web, one embedded problematic action per task):
  exposure under **action-confirmation 88.5%** against **plan-gating 60.4%**, p<.001. The ability
  to *correct* once a problem is visible does not differ between the two regimes (9.2%–26.4%,
  p=.407). The whole gain is in what people never let start, never in what they catch.
* arXiv 2606.05647 (n=107 real developers, ~5 h task, real sabotage, 4 frontier models): 94% do
  not detect it; with a monitor, 63%. And of the 16 sessions where the monitor alerted
  **correctly**, 9 — 56% — approved the malicious change anyway, 67% of those after minimal
  review.

A prompt that arrives in the middle of work is answered in the middle of work. A plan is read
before anything has happened, when refusing it is still cheap.

**What this gate is, precisely, and what it is not.**

It is a stop before the first tool call, on a statement of intent the model wrote. Refusing it ends
the turn having run nothing.

It is **not** enforcement of that statement. The model can approve-then-deviate, and nothing here
would notice — which is exactly why the per-action gate underneath stays switched on and unchanged.
Read this as *one more thing that must be true before work starts*, never as *approval that covers
what follows*. That distinction is the whole design, and getting it backwards is how the same idea
measures WORSE: arXiv 2608.27443 (n=113, pre-registered) let users pre-authorise what an agent may
do and measured overreach blocked falling from 59.6% to **39.6%**, −20.1 pp [−32.1, −8.1], because
a permission granted in advance is a per-action gate that has been turned off. So:

* BLOCK is untouched. A fixed signature refuses before any approver is consulted
  (``governed_tool.py``), and ``profile.py`` states that ``observe`` applies BLOCKs too. Nothing in
  this module can reach that path, let alone soften it.
* REVIEW is untouched. Every call that would have raised a question still raises it.
* The plan gate only ever ADDS a stop. There is no configuration of it that removes one.

**Why the planning call is safe to make before the human has said anything.** The ``plan`` role is
documented in ``api/roles.py`` as carrying no tools, and this module uses it that way: one
``complete`` call, no registry, no workspace access. The only thing spent before the person sees
the plan is that call.

**Why the approved plan is not turned into a write region.** It is the obvious next step — the plan
names files, so narrow the turn to them — and it is deliberately not in this version. The declared
paths are a model's guess made before it has read anything, so a narrow region built from them would
refuse legitimate writes mid-turn; users would answer that by approving wide scopes, which is the
2608.27443 failure arriving through the side door. Whether declared scopes are accurate enough to
narrow on is a measurement nobody here has made. Until somebody makes it, the plan is shown to a
person and injected into the turn, and the capability envelope is left alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.core.planner import Plan, parse_steps
from chimera.providers.gateway import Message

_PLAN_GATE_SYSTEM = (
    "You are about to work on a codebase. Before doing anything, state what you intend to do as "
    "a short numbered plan of 3-7 concrete steps. Name the files you expect to change and any "
    "command you expect to run. Output ONLY the numbered steps, one per line."
)

REASON = (
    "Approving starts the turn and lets it run these steps. It does NOT pre-approve anything: "
    "every dangerous action still asks separately, and refusing here runs nothing at all."
)


@dataclass(frozen=True)
class PlanVerdict:
    """What the gate decided, and on what.

    ``outcome`` is kept separate from ``approved`` because the three ways to not-approve are
    different events and collapsing them is how a product bug reads as a user decision: a person
    who refused made a choice, a person who never answered was not there, and a planner that
    returned nothing is ours to fix.
    """

    approved: bool
    plan: Plan | None
    outcome: str
    """``approved`` | ``refused`` | ``no_plan``. A timeout arrives as ``refused``: `ask_durably`
    answers False for silence by design, and this module does not invent a distinction the
    mechanism underneath does not report."""


def propose(
    message: str, *, backend: Any, model: str | None = None, context: str = ""
) -> Plan:
    """One tool-free model call: what does it intend to do?

    A distinct system prompt from :data:`chimera.core.planner._PLANNER_SYSTEM`, because the two
    plans are read by different readers. The planner's plan steers a worker; this one is shown to a
    person deciding whether to let the turn start, so it asks for the files and commands that make
    a plan *refusable* rather than merely sensible. That is why this calls the backend directly
    instead of reusing :class:`~chimera.core.planner.Planner`, whose system prompt is fixed — but it
    parses with the planner's own :func:`~chimera.core.planner.parse_steps`, so an approved plan and
    a planner's plan are the same shape and `Plan.from_text` keeps working on either.
    """
    user = f"{context}\n\nTask: {message}" if context else message
    raw = backend.complete(
        [
            Message(role="system", content=_PLAN_GATE_SYSTEM),
            Message(role="user", content=user),
        ],
        model=model,
        temperature=0.2,
    ).content
    return Plan(steps=parse_steps(raw), raw=raw)


def gate(
    message: str,
    *,
    home: Path,
    backend: Any,
    model: str | None = None,
    context: str = "",
    on_plan: Any = None,
    on_asked: Any = None,
    wait_seconds: float,
    ask: Any = None,
) -> PlanVerdict:
    """Propose a plan, show it, and wait for a person. Returns what was decided.

    ``ask`` is injectable so the tests can drive the decision without a filesystem; it defaults to
    :func:`chimera.governance.pending.ask_durably`, which is deliberately the SAME mechanism every
    other approval uses. That reuse is most of the value here: the plan question shows up in the
    desktop's pending-approvals dialog, in ``chimera approve``, in the durable history log and in
    the per-level answer stats, and silence refuses it on the same clock — none of which had to be
    built.

    A planner that returns no steps is ``no_plan`` and does NOT start the turn. Failing open there
    would mean the one case where our own machinery broke is the case that runs unsupervised.
    """
    if ask is None:
        from chimera.governance.pending import ask_durably

        ask = ask_durably

    plan = propose(message, backend=backend, model=model, context=context)
    if not plan.steps:
        return PlanVerdict(approved=False, plan=None, outcome="no_plan")

    if on_plan is not None:
        on_plan(plan)

    approved = bool(
        ask(
            home,
            plan.as_text(),
            REASON,
            on_asked=on_asked,
            wait_seconds=wait_seconds,
            decision="review",
        )
    )
    return PlanVerdict(
        approved=approved, plan=plan, outcome="approved" if approved else "refused"
    )


def as_system_note(plan: Plan) -> str:
    """The approved plan, worded for the turn's SYSTEM prompt.

    System rather than appended to the user's message, for the reason the recalled facts and the
    dropped-image note already carry in ``code_api.build_agent``: ``absorb`` drops system messages
    when it stores the transcript, so this reaches the model for this turn and never becomes part
    of what the user is recorded as having said.

    The wording is load-bearing. "Follow this plan" would be a lie about what the gate does — a
    person approved these steps, and the model is being told which steps those were, not granted
    permission to take them. Every one of them still meets the governance it would have met anyway.
    """
    return (
        "A person reviewed and approved this plan before this turn was allowed to start:\n"
        f"{plan.as_text()}\n"
        "Work to it. If you find you need to do something outside it, say so rather than "
        "quietly doing it — dangerous actions will still be asked about individually."
    )
