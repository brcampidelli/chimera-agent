"""The one place a product surface gets a fusion engine from.

``FusionEngine(gateway)`` with no config reads ``CHIMERA_FUSION_PANEL``, whose default is three
frontier models. That is the right thing for a bench that measures the default panel and the wrong
thing everywhere a user is paying: ``api/roles.py`` records how a profile chosen under a cheap cost
mode silently billed Opus + GPT-5.5 + Gemini through exactly that fallback. Role fusion and the
cascade were fixed by hand; seventeen other CLI call sites and three API ones kept the bare form,
so ``--fuse`` meant "the frontier panel" whatever the cost mode said.

So the panel is decided here, once:

- an explicitly configured ``CHIMERA_FUSION_PANEL`` (or judge, or synthesiser) wins — someone who
  named it meant it, and ``model_fields_set`` is what tells "provided" from "equals the default";
- otherwise the panel is the user's tier ladder, top/mid/weak, deduplicated, and the judge and the
  synthesiser are the top tier;
- every other field — mode, probe_k, thresholds, temperatures, ``blind_panel`` — comes from
  :meth:`FusionConfig.from_settings`, so the two builders cannot drift field by field again. They
  did once: the hand-written copy in ``fusion_for_role`` left out ``blind_panel``, and
  ``CHIMERA_FUSION_BLIND_PANEL=false`` was ignored on every role and cascade path.

``tests/test_every_fusion_engine_is_built_from_the_users_ladder.py`` refuses a bare constructor in
``chimera/cli`` and ``chimera/api`` outside a named allowlist.
"""

from __future__ import annotations

from typing import Any

from chimera.fusion.engine import FusionConfig, FusionEngine


def fusion_config(settings: Any) -> FusionConfig:
    """The fusion config a user's settings mean: their ladder unless they named the models."""
    from chimera.providers.catalog import resolve_tiers

    config = FusionConfig.from_settings(settings)
    explicit: set[str] = set(getattr(settings, "model_fields_set", set()))
    if "fusion_panel" in explicit:
        return config
    ladder = resolve_tiers(settings)
    # dict.fromkeys: dedupe while keeping order. A cost mode can point two tiers at the same model
    # (``cheap`` sets mid == top), and a panel that asks one model the same question twice is paying
    # twice for one opinion and calling the agreement a signal.
    config.panel = list(dict.fromkeys([ladder.top, ladder.mid, ladder.weak]))
    # The judge and the synthesiser follow the ladder only when the user did not name them: an
    # explicit `CHIMERA_FUSION_JUDGE` is a choice, the same as an explicit panel.
    if "fusion_judge" not in explicit:
        config.judge = ladder.top
    if "fusion_synthesizer" not in explicit:
        config.synthesizer = ladder.top
    return config


def fusion_engine(gateway: Any, settings: Any = None) -> FusionEngine:
    """A fusion engine over ``gateway`` whose panel is the user's, never the frontier default.

    With ``settings`` the cast is pinned to them — a caller that resolved settings for this run
    meant those. Without, the engine re-reads the process settings between runs, as the bare engine
    always did: the desktop builds ONE engine at boot, and a cast saved from the Fusion screen has
    to reach the next turn without a relaunch.
    """
    if settings is not None:
        return FusionEngine(gateway, fusion_config(settings))
    from chimera.config import get_settings

    return FusionEngine(gateway, source=lambda: fusion_config(get_settings()))
