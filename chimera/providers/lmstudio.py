"""What the LM Studio on this machine has loaded — the second local runtime the first-run screen asks.

LM Studio serves an OpenAI-compatible API (``http://localhost:1234/v1`` by default), so the question
is ``GET {base}/models`` and the answer is ``{"data": [{"id": "…"}, …]}``. Everything about *how* to
ask is the Ollama probe's, deliberately: the same two bounds (a short connect budget on loopback, a
deadline over the whole call), the same "never raises" contract, and the same separation of
*reachable with nothing loaded* from *nothing answered* — see :mod:`chimera.providers.ollama` for
why each of those is load-bearing. This module only knows the other server's shape.

LM Studio needs no key. LiteLLM's ``lm_studio/`` provider sends a placeholder when none is set, and
reads the base URL from ``LM_STUDIO_API_BASE`` — which the gateway exports from
``Settings.lm_studio_base_url``, so the address this probe asks is the address a turn will use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from chimera.providers.ollama import DEFAULT_TIMEOUT_S, _connect_budget
from chimera.telemetry import get_logger

_log = get_logger("providers.lmstudio")

#: The slug prefix a loaded model is offered under. `lm_studio/<id>` is what LiteLLM routes.
PREFIX = "lm_studio/"

Reason = Literal["", "no_url", "unreachable", "http_error", "not_lm_studio"]


@dataclass(frozen=True)
class LoadedModels:
    """What the configured LM Studio answered, or why it did not. Same shape and contract as
    :class:`~chimera.providers.ollama.InstalledModels`."""

    base_url: str
    reachable: bool
    models: tuple[str, ...] = ()
    reason: Reason = ""


def loaded_models(base_url: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> LoadedModels:
    """Ask the LM Studio at ``base_url`` what it has loaded. Never raises."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return LoadedModels("", False, reason="no_url")

    try:
        import httpx
    except ImportError:  # pragma: no cover — httpx is a hard dependency
        return LoadedModels(base, False, reason="unreachable")

    from chimera.concurrency import call_with_deadline

    budget = httpx.Timeout(timeout_s, connect=_connect_budget(base, timeout_s))
    try:
        response = call_with_deadline(lambda: httpx.get(f"{base}/models", timeout=budget), timeout_s)
    except Exception as exc:  # noqa: BLE001 — an absent LM Studio is a normal state, not a 500
        _log.debug("lm studio model list failed at %s: %s", base, exc)
        return LoadedModels(base, False, reason="unreachable")
    if response.status_code >= 400:
        _log.debug("lm studio model list at %s answered %s", base, response.status_code)
        return LoadedModels(base, False, reason="http_error")

    try:
        entries = response.json()["data"]
    except Exception:  # noqa: BLE001 — a 200 from something that is not an OpenAI-shaped server
        return LoadedModels(base, False, reason="not_lm_studio")
    if not isinstance(entries, list):
        return LoadedModels(base, False, reason="not_lm_studio")

    ids: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict) and isinstance(ident := entry.get("id"), str) and ident.strip():
            ids.add(ident.strip())
    return LoadedModels(base, True, models=tuple(sorted(ids)))
