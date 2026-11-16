"""Whether a run starts with the planner: one rule, read by every autonomous entrance.

``AutonomousConfig.use_planner`` was ``True`` everywhere and only ``chimera solve --no-plan`` could
turn it off; lanes, the app's autonomous run and workflow solve steps had no switch at all. The
switch now exists on each of them, as a tri-state: ``True`` / ``False`` are the caller's explicit
word, ``None`` means "use the default", and the default is decided here and only here.

The default is ``on``. Whether a run that carries an executable verifier should start with the
planner **off** is a question ``bench/planner_ab`` answered; its verdict decides
:data:`PLANNER_OFF_WHEN_VERIFIED` and the numbers behind it are recorded below, not in a commit
message.

Measured, not assumed (``bench/planner_ab/RESULTS.md``, margin -5 pp on pass rate, 90% t interval,
23 tasks x k=3): DeepSeek v3.2 no-plan minus plan +5.8 pp [-2.0, +13.6] (passes the margin); GLM-5.3-flash
-1.5 pp [-8.2, +5.3] (does NOT: the lower limit is below -5 pp). Cost of no-plan / plan 0.97 and 0.85.
Not adopted: one executor failed, so the constant stays False. Qwen3-coder-next was not run (its
pilot projected US$ 4.2 against a remaining ceiling of US$ 2.8, pre-registered all-or-nothing).
"""

from __future__ import annotations

#: When True, a run that has an executable verifier and no explicit ``use_planner`` skips the planning
#: call. Left ``False`` unless ``bench/planner_ab`` met its pre-registered non-inferiority rule on two
#: model families (PREREGISTRATION.md section 5).
PLANNER_OFF_WHEN_VERIFIED: bool = False


def resolve_use_planner(explicit: bool | None, *, has_verifier: bool) -> bool:
    """The planner switch for one run.

    ``explicit`` wins whichever way it points: a caller who set the flag knows something this rule
    does not, and a default that overrode a stated choice would be a bug that looks like a feature.
    With nothing stated, the planner is on unless :data:`PLANNER_OFF_WHEN_VERIFIED` is armed and the
    run has a verifier to judge its result.
    """
    if explicit is not None:
        return explicit
    return not (PLANNER_OFF_WHEN_VERIFIED and has_verifier)
