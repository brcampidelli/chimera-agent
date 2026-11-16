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


def fusion_config(settings: Any, *, with_efforts: bool = True) -> FusionConfig:
    """The fusion config a user's settings mean: their ladder unless they named the models.

    The rule is written once, in :func:`chimera.providers.routing.fusion_cast`, which is also what
    the routing table reports for ``fusion.panel`` / ``fusion.judge`` / ``fusion.synthesizer`` — and
    where a ``CHIMERA_ROLE_EFFORTS`` entry for those roles is applied. Each fusion stage is a fresh
    model call with its own context, so an effort set here never changes mid-conversation.
    """
    from chimera.providers.routing import fusion_cast

    cast: FusionConfig = fusion_cast(settings, with_efforts=with_efforts)
    return cast


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
