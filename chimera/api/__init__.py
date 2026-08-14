"""Desktop app backend: the FastAPI HTTP+SSE API the React frontend (``apps/desktop``) consumes.

Opt-in (``pip install chimera-agent[desktop]``). This docstring used to say "the core CLI never
imports it unless the user runs ``chimera app``" — and that stopped being true without anyone
noticing, because **importing any leaf of this package executes this file**. `chimera.api.usage`,
`chimera.api.roles`, `chimera.api.sessions`, `chimera.api.posture` and `chimera.api.config_api` hold
nothing FastAPI-shaped, and the CLI reaches for all five; each one was silently dragging in the whole
web stack.

That shipped as a broken Docker image in v0.45.0. The Dockerfile installs `.[full]`, which does not
include `desktop`, so the published gateway crash-looped on `ModuleNotFoundError: No module named
'fastapi'` the moment the cron path imported the usage ledger. The obvious patch — adding `desktop`
to the image — treats the symptom, makes the image heavier, and leaves the next leaf import to
rediscover the same trap.

So the eager re-exports are gone and `build_api_app` resolves on first attribute access. Importing
`chimera.api.usage` now costs what reading a JSONL file should cost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.api.app import build_api_app
    from chimera.api.sessions import SessionManager, SessionMeta, SessionStore

#: name -> (submodule, attribute). Resolved on demand by ``__getattr__``, so the cost of an import
#: is the module you asked for and nothing else. Same shape as ``chimera.governance``.
_LAZY: dict[str, tuple[str, str]] = {
    "build_api_app": ("app", "build_api_app"),
    "SessionManager": ("sessions", "SessionManager"),
    "SessionMeta": ("sessions", "SessionMeta"),
    "SessionStore": ("sessions", "SessionStore"),
}

__all__ = ["build_api_app", "SessionManager", "SessionMeta", "SessionStore"]


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    from importlib import import_module

    return getattr(import_module(f"{__name__}.{module_name}"), attribute)


def __dir__() -> list[str]:
    return sorted(__all__)
