"""The retry ladder: raise reasoning effort first, then climb a rung of single models.

An autonomous run that fails verification retries. Until this module the retry was "escalated to
fusion" on an agent whose every model call carries tools, and the router sends any turn with tools to
ONE model: the retry ran on the same model at the same effort, and the receipt said it had escalated.
Nothing errored and the log read as if a stronger model had been asked.

What escalation means here (audit plan P0-09, rules 3 and 5):

- **Effort before model.** The cheapest lever is the one on the model already in use:
  ``low -> medium -> high``. A slug with no ``#effort`` suffix is the provider's default, which is
  treated as ``medium``.
- **Then the next single model.** The ladder is ``weak_model`` -> default model -> top of the
  automatic ladder, deduplicated by provider id. A model that climbs starts its new rung at
  ``medium``. A model outside the ladder climbs straight to the top: its position is unknown, and
  guessing "one above" would be a coin toss.
- **Never fusion.** Fusion does not tool-call and is switched off until it beats the single-model
  curve; the worker built here talks to the plain gateway.
- **Only on a NEW attempt.** A retry restarts the context anyway, so the prompt cache is not paid
  twice. Nothing here runs inside a conversation.
- **The ceiling is honest.** At the top rung on ``high`` there is nothing left to raise, and the step
  returns the slug unchanged. The receipt then records the same model twice rather than a step that
  did not happen.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from chimera.providers.catalog import REASONING_EFFORTS, base_slug, split_effort

#: What an unsuffixed slug is treated as: the provider's default sits at the middle of the scale.
DEFAULT_EFFORT = "medium"


def ladder_of(settings: Any) -> list[str]:
    """``[weak, default, top]`` as provider ids, deduplicated, cheapest first.

    ``resolve_tiers`` supplies the weak and top rungs (the user's own, or the cost mode's); the
    middle is ``default_model`` because that is what a plain run starts on, not the tier ladder's mid.
    """
    from chimera.providers.catalog import resolve_tiers

    tiers = resolve_tiers(settings)
    rungs = [tiers.weak, settings.default_model, tiers.top]
    return list(dict.fromkeys(base_slug(r) for r in rungs if r))


def next_step(
    slug: str,
    ladder: Sequence[str],
    *,
    takes_effort: Callable[[str], bool] | None = None,
    avoid: str = "",
) -> str:
    """The slug one escalation above ``slug``; ``slug`` itself when it is already at the ceiling.

    ``takes_effort`` says whether a route is sent the variant at all. When it is not, raising effort
    would change nothing, so the step goes to the next model instead of recording an effort that
    never reached the wire.

    ``avoid`` is the reviewer's model. The editor never climbs ONTO it: a reviewer that is the model
    that wrote the patch grades its own work and its rejection becomes advisory, so a retry that
    reached that rung would turn the reviewer's veto into a success. The rung is skipped, and when
    nothing above it is left the step stays where it is.
    """
    base, effort = split_effort(slug)
    reviewer = base_slug(avoid) if avoid else ""
    if base == reviewer:
        return slug  # already on the reviewer's model: any step from here would stay on it
    accepts = takes_effort(base) if takes_effort is not None else True
    current = effort or DEFAULT_EFFORT
    if accepts and current != REASONING_EFFORTS[-1]:
        return f"{base}#{REASONING_EFFORTS[REASONING_EFFORTS.index(current) + 1]}"
    rungs = list(ladder)
    if not rungs:
        return slug
    candidates = rungs[rungs.index(base) + 1 :] if base in rungs else rungs[-1:]
    candidates = [rung for rung in candidates if rung != base and rung != reviewer]
    if not candidates:
        return slug
    target = candidates[0]
    keeps_effort = takes_effort is None or takes_effort(target)
    return f"{target}#{DEFAULT_EFFORT}" if keeps_effort else target


class Escalation:
    """The model each attempt asks for, and a worker built lazily for it.

    ``make_worker(slug)`` returns an agent that calls ``slug`` on a single-model backend. Workers are
    built when an attempt first needs one, so a run that never retries pays for nothing, and cached
    per slug so a retry that lands on the same slug reuses its agent.

    What each attempt asked for is fixed the first time it is asked, so a later observation cannot
    rewrite an attempt that already ran.
    """

    def __init__(
        self,
        first_model: str,
        ladder: Sequence[str],
        make_worker: Callable[[str], Any],
        *,
        takes_effort: Callable[[str], bool] | None = None,
    ) -> None:
        self.first_model = first_model
        self._ladder = list(ladder)
        self._make_worker = make_worker
        self._takes_effort = takes_effort
        self._workers: dict[str, Any] = {}
        self._asked: dict[int, str] = {1: first_model}
        self._floor = first_model
        #: The reviewer's model, read when a step is computed; the editor never climbs onto it.
        self.reviewer_of: Callable[[], str] | None = None

    def _rank(self, slug: str) -> tuple[int, int] | None:
        """Where ``slug`` sits on the ladder (rung, effort), or None when it is not on it."""
        base, effort = split_effort(slug)
        if base not in self._ladder:
            return None
        return self._ladder.index(base), REASONING_EFFORTS.index(effort or DEFAULT_EFFORT)

    def observe(self, answered_model: str) -> None:
        """Note a model that actually ran in the run, so no retry steps down from it.

        ``--escalate-on-tool-loop`` swaps the model INSIDE an attempt. A retry computed only from what
        the attempt asked for would then go back to a rung below the one that already executed.
        """
        seen, floor = self._rank(answered_model), self._rank(self._floor)
        if seen is not None and (floor is None or seen > floor):
            self._floor = answered_model

    def restore(self, attempts: Sequence[Any]) -> None:
        """Rebuild what a resumed run already did from its restored attempts.

        Without it a resume forgets that attempt 1 ended on the top model (`--escalate-on-tool-loop`)
        and the next retry steps down from it, and forgets what each attempt asked for.
        """
        for attempt in attempts:
            asked = str(getattr(attempt, "requested_model", "") or "")
            if asked:
                self._asked[int(attempt.index)] = asked
                self.observe(asked)
            self.observe(str(getattr(attempt, "model", "") or ""))

    def sent(self, slug: str) -> str:
        """``slug`` as the wire will carry it: its effort suffix only when the effort is really sent."""
        base, effort = split_effort(slug)
        if effort and (self._takes_effort is None or self._takes_effort(base)):
            return slug
        return base

    def is_step_up(self, target: str, current: str) -> bool:
        """Whether ``target`` sits strictly above ``current`` on the ladder (unknown positions: no)."""
        above, below = self._rank(target), self._rank(current)
        return above is not None and below is not None and above > below

    def model_for(self, attempt_index: int) -> str:
        """The slug attempt ``attempt_index`` (1-based) asks for."""
        if attempt_index not in self._asked:
            previous = self.model_for(attempt_index - 1)
            seen, floor = self._rank(previous), self._rank(self._floor)
            start = self._floor if floor is not None and (seen is None or floor > seen) else previous
            avoid = self.reviewer_of() if self.reviewer_of is not None else ""
            step = next_step(start, self._ladder, takes_effort=self._takes_effort, avoid=avoid)
            reviewer = base_slug(avoid) if avoid else ""
            if reviewer and base_slug(step) == reviewer:
                # The floor (a model that already ran inside an attempt) is the reviewer's own: step
                # from what the attempt asked for instead, and if that is the reviewer's too, stay.
                step = next_step(previous, self._ladder, takes_effort=self._takes_effort, avoid=avoid)
                if base_slug(step) == reviewer:
                    step = previous
            self._asked[attempt_index] = step
        return self._asked[attempt_index]

    def stepped(self, attempt_index: int) -> bool:
        """Whether attempt ``attempt_index`` asks for something different from the one before it.

        False at the ceiling, where there is nothing left to raise: that retry is a plain retry and
        must not be announced or labelled as an escalation.
        """
        return attempt_index > 1 and self.model_for(attempt_index) != self.model_for(attempt_index - 1)

    def worker_for(self, attempt_index: int) -> Any:
        slug = self.model_for(attempt_index)
        if slug not in self._workers:
            self._workers[slug] = self._make_worker(slug)
        return self._workers[slug]


def build_escalation(
    gateway: Any,
    settings: Any,
    registry: Any,
    config: Any,
    *,
    first_model: str | None = None,
) -> Escalation:
    """The retry ladder for a run whose first attempt is ``Agent(..., config)``.

    Every escalated worker talks to ``gateway`` itself: no router, no cascade, no fusion. A cascade
    answers tool turns on ITS mid tier whatever ``model=`` says, so a worker behind one could not
    honour a step up; the plain gateway honours exactly the slug it is given. ``first_model`` is for
    the caller whose first worker does not run on ``config.model`` (a cascade runs on its mid tier).
    """
    from dataclasses import replace

    from chimera.core.agent import Agent

    first = first_model or config.model or settings.default_model

    def make_worker(slug: str) -> Agent:
        # `escalate_on_tool_loop` swaps the model inside an attempt. On a worker that is already
        # escalated it may only go UP: a target at or below this worker's own rung would make the
        # trip a step down, so the trigger is switched off there.
        target = getattr(config, "escalate_on_tool_loop", None)
        keep = target if target and escalation.is_step_up(target, slug) else None
        return Agent(gateway, registry, replace(config, model=slug, escalate_on_tool_loop=keep))

    route_takes = getattr(gateway, "takes_effort", None)
    thinking = getattr(config, "thinking", None)

    def takes_effort(slug: str) -> bool:
        # The gateway's own answer, thinking included: on OpenRouter `thinking=False` overwrites the
        # effort (a retry that raised it would repeat the call), on an OpenAI route it does not.
        return route_takes(slug, thinking=thinking) if route_takes is not None else True

    escalation = Escalation(first, ladder_of(settings), make_worker, takes_effort=takes_effort)
    return escalation
