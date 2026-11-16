"""A model per role — the honest version of "multi-LLM coding".

Every competitor picks one model and hopes. The tempting way to be different is to put a "fuse"
switch on the coding loop, and it would be a lie, for a reason that is easy to check and easy to
miss: :func:`chimera.fusion.router.RoutedBackend.complete` sends any turn that carries tool schemas
straight to a single model, and :class:`~chimera.fusion.engine.FusionEngine` ignores tool schemas
outright. In a coding loop **every** turn carries tools. Fusion would never fire, and the switch
would report that it had.

So the multi-LLM story here is not "fuse the loop". It is that a coding run is made of jobs with
genuinely different shapes, and one model is rarely best at all of them:

===========  ==========  ==================================================================
Role         Has tools?  What it is, and why fusion does or does not belong
===========  ==========  ==================================================================
explore      yes         Localisation. "Where does X live?" — a search, not a judgement. A
                         cheap model does it well, and the sub-agent returns the finding
                         rather than the search, so the main loop never pays for the hunt.
plan         **no**      Deep, tool-free reasoning whose output is text that can be judged.
                         Fusion belongs here, and already ran here under ``--fuse``.
edit         yes         Writing the patch. Single model, always. **Never fuse this** —
                         synthesising three different patches is how you produce a patch
                         that applies cleanly and means nothing.
review       **no**      Judging a diff. Fusion belongs here too, and the reviewer should
                         not be the model that wrote the code.
verify       no model    ``CommandVerifier`` runs a command and reads its exit code. There
                         is nothing to choose, and that is the point: the thing that decides
                         whether the work was good is the one part with no opinion.
===========  ==========  ==================================================================

That table is the design. It generalises aider's architect/editor split from two roles to five, and
the difference from every "supports N models" claim is the last row: because the verdict is
executable, "did routing help?" is a question this project can answer with a number instead of a
preference. The number is the point; the routing is what gets measured.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.config import Settings

#: The named presets. A profile is a starting point, not a ceiling: every role stays overridable.
Profile = Literal["economy", "balanced", "max"]

DEFAULT_PROFILE: Profile = "balanced"


class RoleModels(BaseModel):
    """One model slug per role. ``None`` anywhere means "the run's default model".

    ``verify`` is deliberately absent rather than nullable — offering a field for it would imply a
    choice exists, and the whole value of an executable verifier is that there isn't one.
    """

    explore: str | None = None
    plan: str | None = None
    edit: str | None = None
    review: str | None = None

    fuse_plan: bool = False
    """Route planning through the fusion engine. Safe: planning carries no tools."""
    fuse_review: bool = False
    """Route the reviewer through fusion. Safe for the same reason, and useful for a different one —
    a panel disagreeing about a diff is exactly the signal a single reviewer cannot give you."""


@dataclass(frozen=True)
class RolePlan:
    """The resolved roles, plus the sentence a UI shows so nobody has to infer it from four fields."""

    models: RoleModels
    profile: Profile | None

    single_model: str | None = None
    """Set when the tier ladder collapsed to ONE model, and therefore no routing happened.

    Carried rather than inferred by each caller, because the fact it stands for is uncomfortable and
    easy to leave unsaid: with one model there is nothing to route, ``review_model_for`` returns
    ``None``, and the Manager reviews with the very model that wrote the patch — the self-grading
    collapse the reviewer rule exists to prevent. A profile selector that still offered three
    choices here would be charging the user attention for a distinction that does not exist.
    """


def resolve(profile: Profile | None, settings: Settings, override: RoleModels | None = None) -> RolePlan:
    """Turn a profile into concrete models, then let any explicit override win field by field.

    The tiers come from the routing table (``resolve_tiers`` underneath), which already honours the
    user's cost mode and explicit per-tier model settings — so a profile chosen here never
    contradicts what they configured globally, it just says which tier each ROLE draws from.

    **Effort is fixed here, once.** ``CHIMERA_ROLE_EFFORTS`` entries for ``code.explore|plan|edit|
    review`` are added to the role's slug as ``#effort`` when this function builds the plan, and the
    plan is frozen: a run reads ``plan.models.<role>`` for each call that has its own context
    (explore is a sub-agent, plan and review are single completions, edit is the run's loop from its
    first turn), so nothing re-reads the setting in the middle of a conversation. An explicit
    ``override`` is applied AFTER and names its slug in full, ``#effort`` included.
    """
    if profile is None and override is None:
        return RolePlan(RoleModels(), None)

    if profile is None:
        # Overrides with no profile route exactly the roles that were named and nothing else. The
        # tempting alternative — quietly applying the default profile because *some* routing was
        # asked for — turns "put the editor on this model" into "and silently move the other three
        # as well", which is not what anyone typing one field meant.
        return RolePlan(_merge(RoleModels(), override), None)

    base, single = _profile_base(profile, settings)
    if single is not None:
        # One model on every rung: routing has nothing to route. This is the ordinary state for a
        # user with a single provider key, and pretending otherwise would produce a `max` profile that
        # costs exactly what `economy` costs and reports itself as something else. An explicit
        # per-role override still wins — someone naming a second model is exactly the person who has
        # one. No role effort either: every role runs the run's one model, in one conversation.
        return RolePlan(_merge(RoleModels(), override), profile, single_model=single)
    return RolePlan(_merge(with_role_efforts(base, settings), override), profile)


def _profile_base(profile: Profile, settings: Settings) -> tuple[RoleModels, str | None]:
    """``(models, single_model)`` for a profile, before role efforts and before any override.

    ``single_model`` is the one model when the ladder collapsed to it (and then ``models`` is empty).
    """
    from chimera.providers.catalog import resolve_tiers

    # The light path: only the ladder is needed, so the rest of the routing table is not built.
    ladder = resolve_tiers(settings)

    if ladder.weak == ladder.mid == ladder.top:
        return RoleModels(), ladder.weak

    chosen = profile
    if chosen == "economy":
        base = RoleModels(explore=ladder.weak, plan=ladder.mid, edit=ladder.mid, review=ladder.mid)
    elif chosen == "max":
        # `edit` stays on `mid`, and this is the one place where "max" deliberately does not take the
        # top tier. The ladder ranks models by REASONING strength, which is not the same property as
        # tool-calling — and `edit` is the only role that both carries tools on every turn and has to
        # actually finish. Escalating it was measured, not theorised: with `top` on edit, all three
        # pilot solves burned the full 1800 s wall and produced an empty patch, at US$ 0.16 apiece —
        # cheap because the arm was waiting on reasoning passes rather than working. See PILOT.md.
        #
        # So `max` escalates where reasoning IS the job (plan, review, and explore over `balanced`'s
        # weak tier) and fuses the two tool-free roles. That is the module's own table, applied.
        base = RoleModels(
            explore=ladder.mid,
            plan=ladder.top,
            edit=ladder.mid,
            review=ladder.top,
            fuse_plan=True,
            fuse_review=True,
        )
    else:  # balanced
        base = RoleModels(
            explore=ladder.weak, plan=ladder.top, edit=ladder.mid, review=ladder.top, fuse_plan=True
        )
    return base, None


def with_role_efforts(base: RoleModels, settings: Settings) -> RoleModels:
    """``base`` with each code role's ``CHIMERA_ROLE_EFFORTS`` entry added to its slug.

    A slug that already carries ``#effort`` keeps it. When the reviewer and the editor are the SAME
    slug the reviewer keeps following the editor's final slug: ``review_model_for`` withholds a
    reviewer that is the editor, and a ``code.review`` effort that made the two differ would turn
    "the author grades its own work" into a distinct-looking pair on the same model.
    """
    from chimera.providers.routing import with_role_effort

    explore = with_role_effort(settings, "code.explore", base.explore)
    plan = with_role_effort(settings, "code.plan", base.plan)
    edit = with_role_effort(settings, "code.edit", base.edit)
    review = edit if base.review and base.review == base.edit else with_role_effort(
        settings, "code.review", base.review
    )
    return base.model_copy(update={"explore": explore, "plan": plan, "edit": edit, "review": review})


def _merge(base: RoleModels, override: RoleModels | None) -> RoleModels:
    """Apply only the fields the override actually SET.

    ``exclude_unset`` rather than ``exclude_none`` is the load-bearing part: a partially-filled
    override must not blank the rest of the profile back to "the run's default model", and a field
    explicitly set to None must still be able to say "put this role back on the default".
    """
    if override is None:
        return base
    merged = base.model_dump()
    merged.update(override.model_dump(exclude_unset=True))
    return RoleModels(**merged)


def fusion_for_role(gateway: object, settings: Settings) -> object:
    """A fusion engine whose panel is the USER'S OWN tier ladder — not the frontier default.

    This exists because the first version of role fusion was a lie, and a measured one. A profile
    that said ``plan = <top tier>`` built ``FusionEngine(gateway)`` with no config; the engine then
    fell back to ``FusionConfig.from_settings()``, whose ``CHIMERA_FUSION_PANEL`` default is three
    frontier models. So ``--profile balanced`` — chosen under a cost mode that had picked cheap
    tiers — silently convened Opus + GPT-5.5 + Gemini and billed at their rates. A costing pilot
    caught it only because the arm was inexplicably slow; nothing in the run said which models had
    answered, because a bare engine writes no route log.

    Two failures, one line apart, and they compound: the role's model was **discarded** (a fusion
    panel has no single model to honour, and ``FusionEngine.complete`` drops the argument), and what
    replaced it came from a global default the caller never chose in this context.

    The fix is to make the panel mean what the profile means. A profile is a statement about the
    tier ladder, so a fused role draws its panel from that ladder — weak/mid/top, deduplicated,
    strongest first — and is judged and synthesised by the top tier. Everything else (mode, probe_k,
    thresholds, temperatures) still comes from settings, because those are behaviour the user tuned
    and none of them silently changes which models are billed.

    An **explicit** ``CHIMERA_FUSION_PANEL`` still wins: someone who named a panel meant it. The
    check is ``model_fields_set``, which distinguishes "explicitly provided" from "happens to equal
    the default" — the distinction that made this bug invisible in the first place.
    """
    # The panel logic lives in `chimera.fusion.factory`, which every CLI and API surface now goes
    # through. It used to be written out here field by field, and that copy left out `blind_panel`:
    # `CHIMERA_FUSION_BLIND_PANEL=false` was ignored on every role and cascade path. The factory
    # builds the behaviour fields with `FusionConfig.from_settings(settings)` — the settings we were
    # HANDED, never the process-global ones, so a test's Settings still applies.
    from chimera.fusion.factory import fusion_engine

    return fusion_engine(gateway, settings)


def review_model_for(plan: RolePlan) -> str | None:
    """The reviewer's model, refusing to be the same one that wrote the patch.

    Generate-and-verify collapses when both are the same model: it grades its own work and agrees
    with itself. When a profile or override has them equal, the reviewer is left on the run's
    default rather than silently mirroring the editor — a weaker independent check beats a stronger
    dependent one, and this is the one place where "the best model" is the wrong answer.
    """
    models = plan.models
    if models.review and models.review == models.edit:
        return None
    return models.review
