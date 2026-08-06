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


def resolve(profile: Profile | None, settings: Settings, override: RoleModels | None = None) -> RolePlan:
    """Turn a profile into concrete models, then let any explicit override win field by field.

    The tiers come from ``resolve_tiers``, which already honours the user's cost mode and explicit
    per-tier model settings — so a profile chosen here never contradicts what they configured
    globally, it just says which tier each ROLE draws from.
    """
    from chimera.providers.catalog import resolve_tiers

    if profile is None and override is None:
        return RolePlan(RoleModels(), None)

    if profile is None:
        # Overrides with no profile route exactly the roles that were named and nothing else. The
        # tempting alternative — quietly applying the default profile because *some* routing was
        # asked for — turns "put the editor on this model" into "and silently move the other three
        # as well", which is not what anyone typing one field meant.
        return RolePlan(_merge(RoleModels(), override), None)

    ladder = resolve_tiers(settings)  # type: ignore[arg-type]
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

    return RolePlan(_merge(base, override), profile)


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
    from chimera.fusion.engine import FusionConfig, FusionEngine
    from chimera.providers.catalog import resolve_tiers

    # Built from the settings we were HANDED, not via FusionConfig.from_settings(), which reads the
    # process-global get_settings(). Mixing the two would take behaviour from one object and models
    # from another — the same class of split-brain that produced the bug this function exists to fix,
    # and untestable besides, since a test's Settings would silently not apply.
    config = FusionConfig(
        panel=list(settings.fusion_panel),
        judge=settings.fusion_judge,
        synthesizer=settings.fusion_synthesizer,
        mode="selective" if settings.fusion_mode == "selective" else "full",
        probe_k=settings.fusion_probe_k,
        agreement_threshold=settings.fusion_agreement_threshold,
        task_typed=settings.fusion_task_typed,
        panel_temperatures=list(settings.fusion_panel_temperatures),
    )
    if "fusion_panel" not in getattr(settings, "model_fields_set", set()):
        ladder = resolve_tiers(settings)  # type: ignore[arg-type]
        # dict.fromkeys: dedupe while keeping order. A cost mode can point two tiers at the same
        # model (``cheap`` sets mid == top), and a panel that asks one model the same question twice
        # is paying twice for one opinion and calling the agreement a signal.
        config.panel = list(dict.fromkeys([ladder.top, ladder.mid, ladder.weak]))
        config.judge = ladder.top
        config.synthesizer = ladder.top
    return FusionEngine(gateway, config)  # type: ignore[arg-type]


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
