"""A :class:`Decider` from the settings: the chosen backend, its measured default model, the shipped
maps with the deployment's own refits on top.

Kept out of ``chimera.decisions.__init__`` on purpose — this is the one module that reaches for the
settings, the gateway and the OpenRouter key, and a caller that only wants the contract (a test, a
bench, a reader of receipts) should not pay for those imports.

The deployment's maps live in ``<home>/decisions/maps.json`` (:func:`maps_path`), written by
:meth:`CalibrationMaps.save` after a fit on the deployment's own labelled rows; a map there with the
same four keys as a shipped one replaces it — a refit on more rows beats the package's fifty-five.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from chimera.decisions.calibration import CalibrationMaps
from chimera.decisions.contract import Decider, DecisionBackend, DecisionCache
from chimera.decisions.log import DecisionLog

BACKENDS = ("local_logprob", "hosted_verbalized", "openrouter_decisions")


def maps_path(settings: Any) -> Path:
    return Path(settings.home) / "decisions" / "maps.json"


def build_backend(settings: Any, *, gateway: Any | None = None) -> DecisionBackend:
    """The backend the settings name, with its measured default model when none is set."""
    name = (settings.decision_backend or "local_logprob").strip()
    model = (settings.decision_model or "").strip()
    if name == "local_logprob":
        from chimera.decisions.local import DEFAULT_MODEL, LocalLogprobBackend

        return LocalLogprobBackend(settings.ollama_base_url, model or DEFAULT_MODEL)
    if name == "hosted_verbalized":
        from chimera.decisions.hosted import HostedVerbalizedBackend

        if gateway is None:
            from chimera.providers import LLMGateway

            gateway = LLMGateway(settings)
        return HostedVerbalizedBackend(gateway, model or settings.fusion_judge)
    if name == "openrouter_decisions":
        from chimera.decisions.openrouter import DEFAULT_MODEL as VENDOR_MODEL
        from chimera.decisions.openrouter import OpenRouterDecisionsBackend

        # The settings read the key the way the gateway does (`.env`, then the environment); the
        # backend never prints it and never stores it anywhere but the request header.
        key = getattr(settings, "openrouter_api_key", None) or os.environ.get("OPENROUTER_API_KEY", "")
        return OpenRouterDecisionsBackend(key, model or VENDOR_MODEL)
    raise ValueError(f"unknown decision backend {name!r}; one of {', '.join(BACKENDS)}")


def build_decider(settings: Any, *, gateway: Any | None = None, log: bool = True) -> Decider:
    """The Decider a surface uses: the settings' backend, the shipped maps under the deployment's
    refits, a per-process cache of readings, and the decision log under ``<home>/decisions/`` —
    every answer written with its raw number, so the deployment's own labels can refit the map
    (study 22, phase 2). ``log=False`` for a caller that only reads (a report, a test)."""
    maps = CalibrationMaps.shipped().merged(CalibrationMaps.load(maps_path(settings)))
    return Decider(
        build_backend(settings, gateway=gateway), maps, cache=DecisionCache(),
        log=DecisionLog.for_home(Path(settings.home)) if log else None,
    )
