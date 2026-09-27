"""The System One models a deployment may choose for ``openrouter_decisions``, asked rather than
remembered — and the check that refuses a choice the backend cannot honour.

OpenRouter lists its decision models on the public index (``output_modalities=decisions``, no key).
Listing is the easy half. The half that matters is what a row claims about each one, because the
screen that renders it is where someone picks the instrument their REVIEW band will read:

**Which questions it answers is known, not guessed.** :mod:`chimera.decisions.openrouter` sends the
Jev request shape — ``noul`` and ``choice`` questions, a Score asked as a Choice over its levels. A
model is selectable only when its family is known to answer that shape (:data:`FAMILIES`). Respan's
Span-01 describes itself as *behaviour scoring* — "the probability that the behavior is present" for
each behaviour you define — which reads as Noul-only, and nothing in this repository has sent it a
request, so it is listed as ``behavior`` and not selectable. A family nobody has classified is listed
and refused the same way: an index entry says it returns decisions, not that it returns OURS.

**A moving alias is not an instrument.** ``~typesafe/jev-latest`` redirects to whatever build is
newest; a map is keyed on the model and a receipt names it, and neither means anything across a
silent swap. The backend's docstring already pins a build; the alias is listed and refused.

**Calibrated means a map exists for that slug**, shipped or refitted by this deployment — one a
different model cannot borrow (``maps.py``: a different model is a different instrument). No map
ships for this backend today, so every row reads uncalibrated until a deployment fits its own.

**Offline is not "there are none".** A failed fetch returns the backend's measured default alone,
flagged ``stale`` with the reason word, so the picker still shows what the backend uses when the
model is left empty — and the refusal check, which reads the same listing, refuses any other slug
rather than accepting one it could not see.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from chimera.decisions.calibration import CalibrationMaps
from chimera.decisions.factory import BACKENDS
from chimera.decisions.openrouter import DEFAULT_MODEL
from chimera.providers.listing import Reason, _price_per_million, fetch_openrouter_index

BACKEND = "openrouter_decisions"

#: What the index is asked for. Filtered again on our side (:func:`_is_decision_model`): the query
#: parameter is the vendor's to change, and a chat model in this list would be offered as a judge.
QUERY = {"output_modalities": "decisions"}

Contract = Literal["jev", "behavior", "unknown"]

#: Slug prefix -> the request shape the family answers. Each entry cites what classified it.
#:
#: - ``typesafe/jev-``: the model the backend was written and measured against (bench arm J).
#: - ``jaredpalmer/kev-``: its index description says it is "served over the same /v1/systemone
#:   contract as TypeSafe's" — the vendor's claim, and the only one there is; selectable, and its
#:   receipts read uncalibrated until a deployment fits a map on its own rows.
#: - ``respan/span-``: "behavior scoring", a probability per behaviour — a Noul's shape at most, and
#:   never sent a request from here. Listed so the person can see it exists; not selectable.
FAMILIES: tuple[tuple[str, Contract], ...] = (
    ("typesafe/jev-", "jev"),
    ("jaredpalmer/kev-", "jev"),
    ("respan/span-", "behavior"),
)

#: The question kinds each contract answers through :mod:`chimera.decisions.openrouter`.
QUESTIONS: dict[Contract, tuple[str, ...]] = {
    "jev": ("noul", "choice", "score"),
    "behavior": ("noul",),
    "unknown": (),
}

#: Why a listed model cannot be chosen — a word, like the listing's reasons, because the desktop
#: renders it in ten languages and the server does not know which one is on screen.
Refusal = Literal["", "alias", "behavior_contract", "unknown_contract"]

#: Seconds a fetched listing is reused, and a failed one. Shorter than the chat-model index's hour:
#: this list is read when someone is choosing, and a family that appears should appear that day.
CACHE_TTL_S = 600.0
CACHE_FAILURE_TTL_S = 60.0


@dataclass(frozen=True)
class SystemOneModel:
    slug: str
    name: str
    #: USD per 1M input tokens; ``None`` when the index quotes no price (never read as free).
    input_per_m: float | None
    #: Context window in tokens; ``None`` when the index says 0 or nothing.
    context: int | None
    description: str
    contract: Contract
    questions: tuple[str, ...]
    #: A moving alias (``~vendor/name-latest``): the build behind it changes without notice.
    alias: bool
    selectable: bool
    refusal: Refusal
    #: A calibration map exists for (openrouter_decisions, slug) — shipped or this deployment's.
    calibrated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug, "name": self.name, "input_per_m": self.input_per_m, "context": self.context,
            "description": self.description, "contract": self.contract, "questions": list(self.questions),
            "alias": self.alias, "selectable": self.selectable, "refusal": self.refusal,
            "calibrated": self.calibrated,
        }


@dataclass(frozen=True)
class SystemOneListing:
    models: tuple[SystemOneModel, ...]
    #: True when the list is the offline fallback, not what the index answered.
    stale: bool = False
    reason: Reason = ""
    #: The slugs the index answered with — ALL of them, selectable or not; what validation reads.
    listed: frozenset[str] = field(default_factory=frozenset)


def contract_of(slug: str) -> Contract:
    bare = slug.lstrip("~")
    return next((c for prefix, c in FAMILIES if bare.startswith(prefix)), "unknown")


def _is_decision_model(entry: dict[str, Any]) -> bool:
    arch = entry.get("architecture")
    modalities = arch.get("output_modalities") if isinstance(arch, dict) else None
    return isinstance(modalities, list) and "decisions" in modalities


def _model(entry: dict[str, Any]) -> SystemOneModel | None:
    slug = entry.get("id")
    if not isinstance(slug, str) or not slug.strip():
        return None
    slug = slug.strip()
    name = entry.get("name")
    pricing = entry.get("pricing")
    context = entry.get("context_length")
    description = entry.get("description")
    contract = contract_of(slug)
    alias = slug.startswith("~")
    refusal: Refusal = (
        "alias" if alias
        else "behavior_contract" if contract == "behavior"
        else "unknown_contract" if contract == "unknown"
        else ""
    )
    return SystemOneModel(
        slug=slug,
        name=name.strip() if isinstance(name, str) and name.strip() else slug,
        input_per_m=_price_per_million(pricing.get("prompt")) if isinstance(pricing, dict) else None,
        context=int(context) if isinstance(context, int | float) and context > 0 else None,
        description=description.strip() if isinstance(description, str) else "",
        contract=contract,
        questions=QUESTIONS[contract],
        alias=alias,
        selectable=refusal == "",
        refusal=refusal,
    )


def _fallback(reason: Reason) -> SystemOneListing:
    """The backend's measured default, alone — what an empty model means, known without a network."""
    contract = contract_of(DEFAULT_MODEL)
    default = SystemOneModel(
        slug=DEFAULT_MODEL, name=DEFAULT_MODEL, input_per_m=None, context=None, description="",
        contract=contract, questions=QUESTIONS[contract], alias=False, selectable=True, refusal="",
    )
    return SystemOneListing(models=(default,), stale=True, reason=reason, listed=frozenset({DEFAULT_MODEL}))


# (fetched_at, listing) — maps are applied per call, so a refit shows without waiting for the TTL.
_cache: tuple[float, SystemOneListing] | None = None


def _fetch() -> SystemOneListing:
    entries, reason = fetch_openrouter_index(params=dict(QUERY))
    if reason:
        return _fallback(reason)
    models = [m for e in entries if _is_decision_model(e) and (m := _model(e)) is not None]
    if not models:
        # A 200 with no decision model is the index changing under us, not the family vanishing;
        # the default is what an empty setting resolves to either way.
        return _fallback("unreadable")
    return SystemOneListing(models=tuple(models), listed=frozenset(m.slug for m in models))


def list_models(maps: CalibrationMaps | None = None) -> SystemOneListing:
    """The System One models, cached briefly, each marked calibrated against ``maps``. Never raises."""
    global _cache
    now = time.monotonic()
    if _cache is not None:
        fetched_at, cached = _cache
        if now - fetched_at < (CACHE_FAILURE_TTL_S if cached.stale else CACHE_TTL_S):
            return _with_maps(cached, maps)
    fresh = _fetch()
    _cache = (now, fresh)
    return _with_maps(fresh, maps)


def _with_maps(listing: SystemOneListing, maps: CalibrationMaps | None) -> SystemOneListing:
    store = maps if maps is not None else CalibrationMaps.shipped()
    mapped = {m.model for m in store if m.backend == BACKEND}
    return replace(listing, models=tuple(replace(m, calibrated=m.slug in mapped) for m in listing.models))


def check_choice(backend: str, model: str, listing: SystemOneListing) -> None:
    """``ValueError`` naming why, when (backend, model) is a choice the factory cannot honour.

    Only the System One backend's model is checked against the index: the other two take an Ollama
    tag or a gateway slug, which this list does not describe. What IS refused for them is a System
    One slug — ``local_logprob`` with ``typesafe/jev-1.13`` would ask Ollama for a model it does not
    have, and the halt would read as the band being down rather than as the setting being wrong.
    """
    backend = backend.strip()
    model = model.strip()
    if backend not in BACKENDS:
        raise ValueError(f"unknown decision backend {backend!r}; one of {', '.join(BACKENDS)}")
    if backend != BACKEND:
        if model and (model in listing.listed or model == DEFAULT_MODEL):
            raise ValueError(
                f"{model} is a System One model; choose {BACKEND} for it, or leave the model empty "
                f"for {backend}'s default"
            )
        return
    if not model:
        return  # the backend's measured default
    if model not in listing.listed:
        where = "OpenRouter's index could not be reached to check it" if listing.stale else "not in OpenRouter's list"
        raise ValueError(f"{model} is not a listed System One model ({where}); leave it empty for {DEFAULT_MODEL}")
    chosen = next(m for m in listing.models if m.slug == model)
    if not chosen.selectable:
        why = {
            "alias": "a moving alias — the build behind it changes without notice, so a map or a receipt "
            "for it names nothing; choose a pinned build",
            "behavior_contract": "a behaviour-scoring model; Chimera's Decisions client sends Noul/Choice "
            "questions and has never been measured against its answer shape",
            "unknown_contract": "a family whose request shape Chimera has not verified",
        }[chosen.refusal]
        raise ValueError(f"{model} cannot be chosen: {why}")
