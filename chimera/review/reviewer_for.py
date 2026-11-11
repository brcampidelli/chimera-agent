"""The reviewer a review of this install's configured models would use: the one place that reads
the settings, the tier ladder and the provider keys into a :func:`~chimera.review.family.choose_reviewer`
call. The terminal's ``chimera review`` and the Code screen's ``/code-review`` both call it, so the
two cannot pick different models for the same change.
"""

from __future__ import annotations

from chimera.review.family import ReviewerChoice


def reviewer_for(author: str, explicit: str = "") -> ReviewerChoice:
    """The reviewer for a change written by ``author``, read from this install's settings."""
    from chimera.config import get_settings
    from chimera.providers.catalog import CATALOG, resolve_tiers
    from chimera.providers.discovery import is_local_model
    from chimera.review.family import MEASURED_REVIEWERS, choose_reviewer

    settings = get_settings()
    measured = list(MEASURED_REVIEWERS)
    ladder = resolve_tiers(settings)
    rungs = [ladder.top, ladder.mid, ladder.weak]
    panel = list(settings.fusion_panel)
    catalogue = [e.slug for tier in ("top", "mid") for e in CATALOG if e.tier == tier]
    providers = settings.configured_providers()
    reachable = None
    if providers:
        # Only a slug the configured keys can call; the same test the tier resolver applies.
        reachable = {
            m for m in (*measured, *rungs, *panel, *catalogue)
            if m.split("/", 1)[0] in providers or is_local_model(m)
        }
    return choose_reviewer(
        author, explicit=explicit, setting=settings.review_model, measured=measured,
        ladder=rungs, panel=panel, catalogue=catalogue, reachable=reachable,
    )
