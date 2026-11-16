"""One table that says which model each ROLE runs on, and why.

Today the answer is spread over sixteen ``*_model`` settings and eight resolvers: the tier ladder in
:mod:`chimera.providers.catalog`, the escalation rungs, the fusion cast, the reviewer, the verified
answers' escalation model, the decision backend's default, the role profiles. Each is right on its
own; nothing lets a person (or a screen) read them together, and a question as plain as "where did
this slug come from, my ``.env`` or the product?" has no single place to ask.

:class:`RoutingTable` is that place, and in this first slice it is a READER only. It **delegates** to
the functions that decide today (``resolve_tiers``, ``ladder_of``, ``fusion_config``,
``escalation_model``, ``default_model_for``, ``roles.resolve``) and reimplements none of them, so the
table cannot disagree with what runs. ``tests/test_routing_table_is_what_ran_before.py`` pins that
over a matrix of configurations and a committed snapshot; no consumer reads the table yet.

What the table ADDS is provenance. Every route says where its model came from (:data:`Origin`) and,
independently, where its reasoning effort came from, because the two are chosen separately:

``request`` > ``job`` > ``agent`` > ``env`` > ``config`` > ``default``

* ``env`` — the variable was in the process environment when Chimera started
  (:func:`chimera.config.pinned_by_environment`). The Settings screen cannot change it durably.
* ``config`` — a value Settings was handed that did not come from the starting environment: the
  ``.env`` file, or a change saved from the screen after startup.
* ``default`` — nobody set it; ``via`` says which preset or derivation produced it.
* ``request`` / ``job`` / ``agent`` — an override passed to :meth:`RoutingTable.route`. Nothing passes
  one yet; the arguments exist so the precedence is written down once, before the consumers arrive.

Effort is read only from the ``#effort`` suffix of a slug (``openrouter/x/y#high``), which is how it
travels today. Above all of this, and reused rather than restated, sit the owner's deployment policy
(a denied provider's rung is replaced), reachability by the configured keys, and the rule that an
editor never escalates onto its reviewer's model.

**What may raise.** ``routing_table()`` never raises because of a role the process is not using: a
fusion cast, the verified-answers model or the decision backend that cannot be resolved becomes a
route whose ``via`` says ``unavailable: <error>``. The ONE exception it lets through is the one the
legacy resolver already raised, ``ProviderDeniedError`` from ``resolve_tiers`` when the owner's policy
leaves no permitted model at all: there is no ladder to report, and answering with a denied slug
would be worse than refusing.

The module imports nothing from the layers above it at import time; everything it delegates to is
imported inside :func:`build_routing_table`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from chimera.providers.catalog import ReasoningEffort, TierLadder, split_effort

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.config import Settings

Origin = Literal["default", "env", "config", "agent", "job", "request"]
Kind = Literal["chat", "embedding", "media", "local", "list"]

#: Strongest first. An override from a closer scope replaces the model of a further one.
PRECEDENCE: tuple[Origin, ...] = ("request", "job", "agent", "env", "config", "default")


@dataclass(frozen=True)
class Route:
    """The model one role runs on, and where that choice came from."""

    role: str
    model: str
    """The provider's model id, WITHOUT a ``#effort`` suffix (``split_effort`` of what was chosen).
    Empty only for a role whose model is picked at use time (the reviewer)."""
    effort: ReasoningEffort | None
    origin: Origin
    effort_origin: Origin
    via: str
    """One line saying how the model was reached: the setting, the preset, or the derivation."""
    inherits: str = ""
    """The role this one takes its model from while it has none of its own; empty when it has one."""
    kind: Kind = "chat"

    @property
    def slug(self) -> str:
        """What a consumer hands the gateway: the model, plus ``#effort`` when there is one."""
        return f"{self.model}#{self.effort}" if self.effort else self.model


@dataclass(frozen=True)
class RoleSpec:
    """One row of the registry: a role and the setting (if any) that names it."""

    id: str
    how: str
    """Which resolver reads it: ``tier`` ``default`` ``inherit`` ``field`` ``review`` ``fusion``
    ``verified`` ``decisions`` ``list`` ``escalation``."""
    field: str = ""
    kind: Kind = "chat"
    inherits: str = ""
    """For a role with no model of its own: the role it follows."""


#: Every role the table answers. Adding a ``*_model`` setting without a row here is caught by the
#: ratchet test, which counts the places that read those fields.
ROLES: tuple[RoleSpec, ...] = (
    # The ladder and where a plain run starts.
    RoleSpec("tier.weak", "tier", "weak_model"),
    RoleSpec("default", "default", "default_model"),
    RoleSpec("tier.mid", "tier", "mid_model"),
    RoleSpec("tier.top", "tier", "orchestrator_model"),
    RoleSpec("escalation", "escalation", kind="list"),
    # Work. None of these has a model of its own today; they say so rather than pretend.
    RoleSpec("chat", "inherit", inherits="default"),
    RoleSpec("code.explore", "inherit", inherits="default"),
    RoleSpec("code.plan", "inherit", inherits="default"),
    RoleSpec("code.edit", "inherit", inherits="default"),
    RoleSpec("code.review", "inherit", inherits="default"),
    RoleSpec("review", "review", "review_model"),
    RoleSpec("subagent", "inherit", inherits="default"),
    RoleSpec("orchestrator", "inherit", inherits="tier.top"),
    RoleSpec("worker", "inherit", inherits="tier.mid"),
    RoleSpec("cron", "inherit", inherits="default"),
    RoleSpec("design", "inherit", inherits="default"),
    RoleSpec("verify_escalation", "verified", "verified_answers_escalate_model"),
    RoleSpec("decisions", "decisions", "decision_model"),
    # Fusion.
    RoleSpec("fusion.panel", "fusion", "fusion_panel", kind="list"),
    RoleSpec("fusion.judge", "fusion", "fusion_judge"),
    RoleSpec("fusion.synthesizer", "fusion", "fusion_synthesizer"),
    RoleSpec("transfer.panel", "list", "transfer_panel", kind="list"),
    RoleSpec("fallback", "list", "fallback_models", kind="list"),
    # Voice and the other modalities.
    RoleSpec("voice", "field", "voice_model", inherits="conversation"),
    RoleSpec("voice_work", "field", "voice_work_model", inherits="conversation"),
    RoleSpec("chat_voice", "field", "chat_voice_model", kind="local"),
    RoleSpec("complete", "field", "complete_model", kind="local"),
    RoleSpec("embed", "field", "embed_model", kind="embedding"),
    RoleSpec("image", "field", "image_model", kind="media"),
    RoleSpec("image_local", "field", "image_model_local", kind="local"),
    RoleSpec("tts", "field", "tts_model", kind="media"),
    RoleSpec("audio", "field", "audio_model", kind="media"),
    RoleSpec("video", "field", "video_model", kind="media"),
)

ROLE_IDS: tuple[str, ...] = tuple(spec.id for spec in ROLES)


_LOCAL_PATH = re.compile(r"^(?:[/~]|\.{1,2}[/\\]|[A-Za-z]:[/\\]|\\\\|file://)")


# Up to whitespace, a quote or an angle bracket, and no further: stopping at `)` or `]` as well left
# the rest of a query OUTSIDE the match (`?token=abc)def456` published `)def456`). The query is dropped
# whole, so a closing bracket swallowed with it costs a cosmetic character at most.
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s'\"<>]+")
# A path between quotes runs to the closing quote, spaces included: `repr()` of a setting
# (`unknown decision backend '/home/x/Client Name/backend'`) is how a value lands in a message, and
# cutting at the first space published the folder names after it.
_QUOTED_PATH = re.compile(r"""(['"])((?:[A-Za-z]:[/\\]|\\\\|~[/\\]|\.{1,2}[/\\]|/)[^'"]*)\1""")
_PATH_IN_TEXT = re.compile(
    r"(?<![\w:/.\\])(?:[A-Za-z]:[/\\]|\\\\|~[/\\]|\.{1,2}[/\\]|/(?=[^\s'\"<>/]))[^\s'\"<>]*"
)


def _bare_url(match: re.Match[str]) -> str:
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(match.group(0))
        host = parts.hostname or ""
        netloc = f"{host}:{parts.port}" if parts.port else host
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    except ValueError:
        return "[redacted]"


def _file_name(path: str) -> str:
    return re.split(r"[/\\]+", path.rstrip("/\\"))[-1] or "?"


def public_text(text: str) -> str:
    """Any string that leaves through the public view: a path is its file name, a URL has no
    ``user:pass@`` and no query, and the rest goes through the repo's redactor.

    Free text is what leaks: ``ProviderDeniedError`` names the whole model slug, and ``via`` repeats
    the value of a setting. So this works INSIDE a sentence, not only on a value that is one.
    """
    if not text:
        return text
    from chimera.core.redact import redact

    text = _URL.sub(_bare_url, text)
    text = _QUOTED_PATH.sub(lambda m: m.group(1) + _file_name(m.group(2)) + m.group(1), text)
    text = _PATH_IN_TEXT.sub(lambda m: _file_name(m.group(0)), text)
    return redact(text)


def public_model(model: str) -> tuple[str, bool]:
    """``(what a screen may show, is it a local file)`` for a role's model.

    A role's "model" can be a path on this machine (a Piper voice, a diffusers checkpoint): the
    execution needs the whole path, but the API, the Settings panel and ``chimera models`` must not
    publish the folder names in it, which can name a client. Only the file name is shown, flagged as a
    local file. Anything else is :func:`public_text`.
    """
    if not model:
        return model, False
    if _LOCAL_PATH.match(model) and "://" not in model.split("/", 1)[0]:
        return _file_name(model), True
    return public_text(model), False


def _publish(value: Any, key: str = "") -> Any:
    """Every string of a public answer, wherever it sits. A field added later is covered by default."""
    if isinstance(value, str):
        return public_model(value)[0] if key == "model" else public_text(value)
    if isinstance(value, dict):
        return {k: _publish(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_publish(v, key) for v in value]
    return value


def public_routing(settings: Settings) -> dict[str, Any]:
    """The ONE public answer: ``GET /api/routing``, the Settings panel and ``chimera models`` all
    render this and nothing else, so no field reaches a screen except through :func:`_publish`.

    ``ProviderDeniedError`` is the table's one deliberate raise (the deployment policy leaves no model
    for the ladder). It is answered as data (``denied``), not as a server error: the screen has to say
    what the policy did, and a 500 would read as a fault in the product. Its message names the whole
    model, URL credentials included, which is why it goes through the same sanitiser.
    """
    from chimera.providers.gateway import ProviderDeniedError

    try:
        table = settings.routing_table()
    except ProviderDeniedError as exc:
        denied: dict[str, Any] = _publish(
            {"rows": [], "ladder_source": "", "cost_mode": settings.cost_mode, "denied": str(exc)}
        )
        return denied
    answer: dict[str, Any] = _publish({
        "rows": table.describe(), "ladder_source": table.ladder_source,
        "cost_mode": settings.cost_mode, "denied": "",
    })
    return answer


@dataclass(frozen=True)
class RoutingTable:
    """The resolved routes of one :class:`~chimera.config.Settings`, at the moment it was built.

    There is no cache: ``Settings`` is read live, so a table is built per question
    (``settings.routing_table()``) and thrown away.
    """

    routes: Mapping[str, Route]
    """One row per role, in registry order. A list role is summarised here (its model is its slugs,
    comma-joined) and spelled out in :attr:`lists`."""
    lists: Mapping[str, tuple[Route, ...]]
    ladder_source: str
    """``TierLadder.source``: ``override`` | ``preset`` | ``fallback_single_model``."""
    _tiers: TierLadder = field(repr=False, compare=False)
    _rungs: tuple[str, ...] = field(repr=False, compare=False)
    _settings: Any = field(repr=False, compare=False)

    def route(
        self,
        role: str,
        *,
        request: str | None = None,
        job: str | None = None,
        agent: str | None = None,
    ) -> Route:
        """The route for ``role``; a closer scope's slug replaces the model (``request`` > ``job`` >
        ``agent`` > the table).

        The model and the effort are independent: an override slug WITH a ``#effort`` suffix sets
        both, one without it changes the model and leaves the effort where the table had it.
        """
        try:
            base = self.routes[role]
        except KeyError:
            raise KeyError(f"unknown role {role!r}; one of {', '.join(ROLE_IDS)}") from None
        scopes: tuple[tuple[Origin, str | None], ...] = (
            ("request", request), ("job", job), ("agent", agent),
        )
        for origin, slug in scopes:
            chosen = (slug or "").strip()
            if not chosen:
                continue
            model, effort = split_effort(chosen)
            return Route(
                role=role, model=model,
                effort=effort if effort is not None else base.effort,
                origin=origin,
                effort_origin=origin if effort is not None else base.effort_origin,
                via=f"{origin} override", kind=base.kind,
            )
        return base

    def tiers(self) -> TierLadder:
        """Identical to ``resolve_tiers`` for the settings this was built from."""
        return self._tiers

    def escalation_rungs(self) -> list[str]:
        """Identical to ``chimera.core.escalation.ladder_of`` for the same settings."""
        return list(self._rungs)

    def rows(self) -> list[Route]:
        """Every role's route in registry order, list roles as their summary row."""
        return list(self.routes.values())

    def members(self, role: str) -> tuple[Route, ...]:
        """The slugs of a list role (panel, escalation, fallback), one route each; empty for any other."""
        return self.lists.get(role, ())

    def effort_sent(self, route: Route) -> bool | None:
        """Whether the gateway would put ``route``'s effort on the wire: ``None`` when it has none.

        The same rule the escalation ladder asks (``gateway.route_takes_effort``), without a gateway:
        building one exports the configured keys into the process environment, which a read of the
        table must not do. ``thinking`` is a property of a run, so it is not part of this answer.
        """
        if route.effort is None or not route.model:
            return None
        from chimera.providers.gateway import route_takes_effort

        return route_takes_effort(route.model, self._settings)

    def describe(self) -> list[dict[str, Any]]:
        """Every row as plain data, for the screen and ``GET /api/routing``: the route, whether its
        effort would be sent, and each member of a list role.

        The PUBLIC form: a model that is a local path is its file name with ``local_file`` set, and a
        URL has no credentials (:func:`public_model`). The table itself keeps the real value.
        """
        out: list[dict[str, Any]] = []
        for route in self.rows():
            model, local = public_model(route.model) if route.kind != "list" else (route.model, False)
            members: list[dict[str, Any]] = []
            for m in self.members(route.role):
                name, member_local = public_model(m.model)
                members.append({
                    "model": name, "effort": m.effort, "effort_sent": self.effort_sent(m),
                    "local_file": member_local,
                })
            if route.kind == "list":
                model = ",".join(f"{x['model']}#{x['effort']}" if x["effort"] else x["model"] for x in members)
            out.append({
                "role": route.role, "model": model, "effort": route.effort,
                "origin": route.origin, "effort_origin": route.effort_origin, "via": route.via,
                "inherits": route.inherits, "kind": route.kind,
                "effort_sent": self.effort_sent(route), "local_file": local, "members": members,
            })
        return _publish(out)  # type: ignore[no-any-return]

    def profile_routes(self, profile: Any) -> dict[str, Route]:
        """The four code roles a profile (``economy`` | ``balanced`` | ``max``) would run on.

        What the CONSUMERS use, not an intermediate step: ``roles.models.<role> or <the run's model>``
        for explore, plan and edit, and ``review_model_for(plan) or <the run's model>`` for review.
        The last matters: ``resolve`` puts the review rung on ``tier.mid`` under ``economy``, and when
        that is also the editor's model ``review_model_for`` returns ``None`` (a reviewer that is the
        author grades its own work) and the run's own model reviews instead.

        This is not what a run uses unless it was given a profile; the table's ``code.*`` rows
        describe the run that was given none.
        """
        from chimera.api.roles import resolve, review_model_for

        plan = resolve(profile, self._settings)
        chosen = {
            "explore": plan.models.explore, "plan": plan.models.plan, "edit": plan.models.edit,
            "review": review_model_for(plan),
        }
        default = self.routes["default"]
        out: dict[str, Route] = {}
        for name, slug in chosen.items():
            role = f"code.{name}"
            if slug:
                model, effort = split_effort(slug)
                out[role] = Route(role, model, effort, "default", "default", f"profile {profile}")
            else:
                why = "the reviewer would be the editor" if name == "review" else "the profile leaves it unset"
                out[role] = Route(
                    role, default.model, default.effort, default.origin, default.effort_origin,
                    f"profile {profile}: {why}, so the run's model", inherits="default",
                )
        return out


# --- building ---------------------------------------------------------------------------------


def _origin_of(settings: Settings, name: str) -> Origin:
    """``env`` when the variable was in the starting environment, ``config`` when Settings was handed
    the value some other way, ``default`` when nobody set it."""
    from chimera.config import pinned_by_environment

    info = type(settings).model_fields[name]
    alias = info.validation_alias
    if isinstance(alias, str) and pinned_by_environment([alias]):
        return "env"
    return "config" if name in settings.model_fields_set else "default"


def _route(
    role: str, slug: str, origin: Origin, via: str, *, inherits: str = "", kind: Kind = "chat"
) -> Route:
    model, effort = split_effort(slug.strip())
    return Route(
        role=role, model=model, effort=effort, origin=origin,
        effort_origin=origin if effort else "default", via=via, inherits=inherits, kind=kind,
    )


def _summary(role: str, members: list[Route], origin: Origin, via: str) -> Route:
    return Route(
        role=role, model=",".join(m.slug for m in members), effort=None, origin=origin,
        effort_origin="default", via=via, kind="list",
    )


def _tier_route(settings: Settings, spec: RoleSpec, tiers: TierLadder) -> Route:
    """A rung of the ladder. Its origin is the setting's when the user typed one; otherwise the
    preset's, and ``via`` says so — or says the rung was replaced after the fact."""
    from chimera.providers.catalog import _PRESETS

    rung = {"tier.weak": tiers.weak, "tier.mid": tiers.mid, "tier.top": tiers.top}[spec.id]
    typed = str(getattr(settings, spec.field) or "")
    mode = settings.cost_mode if settings.cost_mode in _PRESETS else "auto"
    expected = typed or {
        "tier.weak": _PRESETS[mode].weak, "tier.mid": _PRESETS[mode].mid, "tier.top": _PRESETS[mode].top,
    }[spec.id]
    if rung != expected:
        origin: Origin = _origin_of(settings, spec.field) if typed else "default"
        via = f"replaced after resolution ({tiers.source}): the deployment policy or the keys moved it"
    elif typed:
        origin, via = _origin_of(settings, spec.field), spec.field
    else:
        origin, via = "default", f"cost_mode {mode} preset"
    return _route(spec.id, rung, origin, via)


def _field_route(settings: Settings, spec: RoleSpec, default: Route) -> Route:
    value = str(getattr(settings, spec.field) or "").strip()
    if value:
        return _route(spec.id, value, _origin_of(settings, spec.field), spec.field, kind=spec.kind)
    if spec.inherits:
        return Route(
            spec.id, default.model, default.effort, "default", default.effort_origin,
            "no model of its own: follows the conversation", inherits=spec.inherits, kind=spec.kind,
        )
    return _route(spec.id, "", "default", f"{spec.field} is empty", kind=spec.kind)


def _inherit_route(spec: RoleSpec, routes: Mapping[str, Route]) -> Route:
    parent = routes[spec.inherits]
    return Route(
        spec.id, parent.model, parent.effort, "default", parent.effort_origin,
        "not differentiated today", inherits=spec.inherits,
    )


def _decisions_route(settings: Settings, spec: RoleSpec) -> Route:
    from chimera.decisions.factory import default_model_for

    backend = (settings.decision_backend or "local_logprob").strip()
    kind: Kind = "local" if backend == "local_logprob" else "chat"
    explicit = (settings.decision_model or "").strip()
    if explicit:
        return _route(spec.id, explicit, _origin_of(settings, spec.field), spec.field, kind=kind)
    try:
        model = default_model_for(settings, backend)
    except ValueError:
        return _route(spec.id, "", "default", f"unknown decision backend {backend!r}", kind=kind)
    return _route(spec.id, model, "default", f"backend {backend} default", kind=kind)


def _verified_route(settings: Settings, spec: RoleSpec) -> Route:
    from chimera.fusion.verified import MEASURED_ESCALATION_MODEL, escalation_model

    slug = escalation_model(settings)
    if (getattr(settings, spec.field) or "").strip():
        return _route(spec.id, slug, _origin_of(settings, spec.field), spec.field)
    measured = slug == MEASURED_ESCALATION_MODEL
    via = "the measured escalation model (an OpenRouter key reaches it)" if measured else "the ladder's top"
    return _route(spec.id, slug, "default", via)


def _fusion_routes(settings: Settings) -> tuple[list[Route], Origin, str, Route, Route]:
    """Panel members, the panel's origin and via, then the judge and the synthesiser.

    The panel's origin is the setting's, never taken from its first member: ``CHIMERA_FUSION_PANEL=""``
    is a valid (empty) panel that ``fusion_config`` accepts, and a table that indexed ``[0]`` would
    raise for a role the process is not even using.
    """
    from chimera.fusion.factory import fusion_config

    config = fusion_config(settings)
    explicit = set(settings.model_fields_set)
    panel_explicit = "fusion_panel" in explicit
    if panel_explicit:
        origin, via = _origin_of(settings, "fusion_panel"), "fusion_panel"
    else:
        origin, via = "default", "the tier ladder (fusion_config)"
    panel = [_route("fusion.panel", s, origin, via) for s in config.panel]
    named: list[Route] = []
    for role, name, value in (
        ("fusion.judge", "fusion_judge", config.judge),
        ("fusion.synthesizer", "fusion_synthesizer", config.synthesizer),
    ):
        derived = not panel_explicit and name not in explicit
        named.append(_route(
            role, value, "default" if derived else _origin_of(settings, name),
            "tier.top via fusion_config" if derived else name,
        ))
    return panel, origin, via, named[0], named[1]


def _unavailable(role: str, exc: Exception, kind: Kind = "chat") -> Route:
    return Route(
        role, "", None, "default", "default", f"unavailable: {type(exc).__name__}", kind=kind
    )


def _guarded(
    settings: Settings, spec: RoleSpec, build: Callable[[Settings, RoleSpec], Route]
) -> Route:
    try:
        return build(settings, spec)
    except Exception as exc:  # see build_routing_table: never raise for a role nobody is using
        return _unavailable(spec.id, exc, spec.kind)


def _list_route(settings: Settings, spec: RoleSpec) -> list[Route]:
    origin = _origin_of(settings, spec.field)
    return [_route(spec.id, s, origin, spec.field) for s in getattr(settings, spec.field)]


def build_routing_table(settings: Settings) -> RoutingTable:
    """Resolve every role in :data:`ROLES` for ``settings``, delegating to the legacy resolvers."""
    from chimera.core.escalation import ladder_of
    from chimera.providers.catalog import resolve_tiers

    tiers = resolve_tiers(settings)
    rungs = tuple(ladder_of(settings))
    fusion: tuple[list[Route], Origin, str, Route, Route] | Exception
    try:
        fusion = _fusion_routes(settings)
    except Exception as exc:  # a fusion cast the process is not using must not hide every other role
        fusion = exc
    routes: dict[str, Route] = {}
    lists: dict[str, tuple[Route, ...]] = {}
    pending: list[RoleSpec] = []
    for spec in ROLES:
        if spec.how == "tier":
            routes[spec.id] = _tier_route(settings, spec, tiers)
        elif spec.how == "default":
            routes[spec.id] = _route(
                spec.id, settings.default_model, _origin_of(settings, spec.field),
                "CHIMERA_DEFAULT_MODEL" if spec.field in settings.model_fields_set
                else "the product's default",
            )
        elif spec.how == "escalation":
            members = [_route(spec.id, s, "default", "ladder_of: [weak, default, top]") for s in rungs]
            lists[spec.id] = tuple(members)
            routes[spec.id] = _summary(spec.id, members, "default", "ladder_of: [weak, default, top]")
        elif spec.how == "fusion":
            if isinstance(fusion, Exception):
                routes[spec.id] = _unavailable(spec.id, fusion, spec.kind)
                if spec.kind == "list":
                    lists[spec.id] = ()
            elif spec.id == "fusion.panel":
                lists[spec.id] = tuple(fusion[0])
                routes[spec.id] = _summary(spec.id, fusion[0], fusion[1], fusion[2])
            else:
                routes[spec.id] = fusion[3] if spec.id == "fusion.judge" else fusion[4]
        elif spec.how == "list":
            members = _list_route(settings, spec)
            lists[spec.id] = tuple(members)
            origin = _origin_of(settings, spec.field)
            routes[spec.id] = _summary(spec.id, members, origin, spec.field)
        elif spec.how == "decisions":
            routes[spec.id] = _guarded(settings, spec, _decisions_route)
        elif spec.how == "verified":
            routes[spec.id] = _guarded(settings, spec, _verified_route)
        elif spec.how == "review":
            value = (settings.review_model or "").strip()
            routes[spec.id] = (
                _route(spec.id, value, _origin_of(settings, spec.field), spec.field)
                if value
                else Route(
                    spec.id, "", None, "default", "default",
                    "chosen at review time, against the author's family (reviewer_for)",
                    inherits="reviewer_for",
                )
            )
        else:
            pending.append(spec)
    for spec in pending:
        routes[spec.id] = (
            _field_route(settings, spec, routes["default"]) if spec.how == "field"
            else _inherit_route(spec, routes)
        )
    ordered = {role: routes[role] for role in ROLE_IDS}
    return RoutingTable(
        routes=MappingProxyType(ordered),
        lists=MappingProxyType(lists),
        ladder_source=tiers.source,
        _tiers=tiers,
        _rungs=rungs,
        _settings=settings,
    )
