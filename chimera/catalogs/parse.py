"""The three repositories' formats, read into :class:`~chimera.catalogs.model.CatalogItemOut`.

Every input here has already been verified against its pin, so the question is not "is this the
file" but "can each entry be shown". One bad entry is skipped with a line in ``problems``; it never
empties the catalogue. Unknown fields are ignored, because a catalogue repository adding a field must
not make an older Chimera's catalogue go blank.

* ``chimera-connectors`` — ``dist/catalog.json``, the CI-generated list of every ``connectors/*.yaml``.
* ``chimera-skills`` — one ``catalog/<id>.json`` per skill (title and summary in pt-BR and en).
* ``chimera-plugins`` — ``.claude-plugin/marketplace.json`` (Claude Code's format, validated by the
  same parser the plugin installer uses) joined with ``catalog/<id>.json`` for category and license.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from chimera.catalogs.categories import normalize_category, normalize_secondary
from chimera.catalogs.model import CatalogItemOut, CatalogSourceOut
from chimera.catalogs.pins import CatalogKind, CatalogPin

_MAX_TEXT = 2000
_OWNER_REPO = r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}"
_REPO = re.compile(rf"^{_OWNER_REPO}$")
_GITHUB_URL = re.compile(rf"^https://github\.com/({_OWNER_REPO})/?$")

CONNECTORS_FILE = "dist/catalog.json"
MARKETPLACE_FILE = ".claude-plugin/marketplace.json"
CATALOG_DIR = "catalog/"


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Auth(_Lenient):
    method: str = ""
    client_restriction: str = ""
    env: list[Any] = Field(default_factory=list)


class _Capability(_Lenient):
    executes: bool | None = None
    money: bool | None = None


class _Categories(_Lenient):
    primary: str = ""
    secondary: list[str] = Field(default_factory=list)


class _Connector(_Lenient):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    vendor: str = ""
    kind: str = ""
    transport: str = ""
    license: str | None = None
    notes: str = ""
    categories: _Categories | None = None
    http: dict[str, Any] | None = None
    stdio: dict[str, Any] | None = None
    auth: _Auth | None = None
    capability: _Capability | None = None
    homepage: str = ""
    docs: str = ""


class _SkillSource(_Lenient):
    repo: str = ""
    stars: int | None = Field(default=None, ge=0)
    stars_read_at: str | None = None


class _Skill(_Lenient):
    id: str = Field(min_length=1, max_length=100)
    title: dict[str, str] | str = ""
    summary: dict[str, str] | str = ""
    category: str = ""
    categories_secondary: list[str] = Field(default_factory=list)
    license: str | None = None
    source: _SkillSource | None = None
    mode: str = ""
    ships_scripts: bool | None = None


class _PluginComponents(_Lenient):
    mcp_servers: list[Any] = Field(default_factory=list)
    hooks: bool | None = None
    script_files: int | None = None


class _PluginStars(_Lenient):
    repo: str = ""
    count: int | None = Field(default=None, ge=0)
    read_at: str | None = None


class _PluginCard(_Lenient):
    id: str = ""
    displayName: str = ""  # noqa: N815 -- the repository's field name
    category: str = ""
    secondary: list[str] = Field(default_factory=list)
    license: str | None = None
    origin: str = ""
    upstream: str = ""
    components: _PluginComponents | None = None
    stars: _PluginStars | None = None


def _text(value: object) -> str:
    """English first, then Portuguese, then whatever string there is; cut to a sane length."""
    if isinstance(value, dict):
        for key in ("en", "pt-BR", "pt"):
            got = value.get(key)
            if isinstance(got, str) and got.strip():
                return got.strip()[:_MAX_TEXT]
        return ""
    return value.strip()[:_MAX_TEXT] if isinstance(value, str) else ""


def _json(data: bytes) -> object:
    return json.loads(data.decode("utf-8-sig"))


def _source(pin: CatalogPin, path: str) -> CatalogSourceOut:
    return CatalogSourceOut(owner=pin.owner, repo=pin.repo, sha=pin.sha, path=path)


def _repo(value: str | None) -> str | None:
    """``owner/repo`` when ``value`` is one (or a bare github.com repository URL), else None."""
    text = (value or "").strip()
    if _REPO.match(text):
        return text
    match = _GITHUB_URL.match(text)
    return match.group(1) if match else None


def _dates(pin: CatalogPin, item_id: str) -> tuple[str | None, str | None]:
    history = pin.history.get(item_id)
    if history is None:
        return None, None
    return history.added_at, history.updated_at


def _pinned_stars(count: int | None, read_at: str | None) -> tuple[int | None, str | None]:
    """A count the catalogue recorded, only together with the day it was read — never one alone."""
    if count is None or not read_at:
        return None, None
    return count, read_at


def _connector_setup_free(c: _Connector, installable: bool) -> bool | None:
    """No secret, nothing that executes, no money: by the catalogue's own record, or None."""
    if not installable:
        return False
    if c.auth is None or c.capability is None:
        return None
    if c.capability.executes is None or c.capability.money is None:
        return None
    return (c.auth.method == "none" and not c.auth.env
            and not c.capability.executes and not c.capability.money)


def _connector_refusal(c: _Connector) -> str | None:
    """Why today's Chimera could not add this connector, or None. Reading it adds nothing."""
    if c.kind == "openapi":
        return ("an OpenAPI connector is added by hand in Connectors (its GET operations only), "
                "not installed from the catalogue")
    if c.kind != "mcp":
        return f"a connector of kind {c.kind or '(none)'!r} is not installable"
    if c.transport == "http":
        if not (c.http or {}).get("url"):
            return "the entry names no server URL"
    elif c.transport == "stdio":
        if not (c.stdio or {}).get("command"):
            return "the entry names no command to run"
    else:
        return f"the {c.transport or 'unnamed'} transport is not supported"
    if c.auth is not None and c.auth.client_restriction == "allowlist":
        return "the vendor admits only the clients on its own allowlist"
    return None


def _connectors(pin: CatalogPin, files: dict[str, bytes]) -> tuple[list[CatalogItemOut], list[str]]:
    data = files.get(CONNECTORS_FILE)
    if data is None:
        return [], []
    try:
        raw = _json(data)
    except ValueError:
        return [], [f"{CONNECTORS_FILE} is not JSON"]
    entries = raw.get("connectors") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return [], [f"{CONNECTORS_FILE} has no 'connectors' list"]
    items: list[CatalogItemOut] = []
    problems: list[str] = []
    for n, entry in enumerate(entries):
        try:
            c = _Connector.model_validate(entry)
        except ValidationError:
            problems.append(f"{CONNECTORS_FILE}: connector #{n} skipped (not a valid entry)")
            continue
        cats = c.categories or _Categories()
        primary = normalize_category(cats.primary)
        refusal = _connector_refusal(c)
        added_at, updated_at = _dates(pin, c.id)
        item = CatalogItemOut(
            id=c.id, kind="connectors", name=c.name, description=c.notes[:_MAX_TEXT],
            author=c.vendor, license=c.license, category=primary,
            categories_secondary=normalize_secondary(cats.secondary, primary=primary),
            source=_source(pin, CONNECTORS_FILE), installable=refusal is None, refusal=refusal,
            # A connector's homepage is usually the vendor's site; only a github.com repository URL
            # names stars that belong to it.
            stars_repo=_repo(c.homepage) or _repo(c.docs),
            added_at=added_at, updated_at=updated_at,
        )
        items.append(item.mark(setup_free=_connector_setup_free(c, refusal is None), own=False))
    return items, problems


def _cards(files: dict[str, bytes], model: type[_Lenient]) -> tuple[dict[str, tuple[str, Any]], list[str]]:
    """``catalog/<id>.json`` files, validated: id -> (path, model)."""
    out: dict[str, tuple[str, Any]] = {}
    problems: list[str] = []
    for path, data in files.items():
        if not path.startswith(CATALOG_DIR):
            continue
        try:
            card = model.model_validate(_json(data))
        except (ValueError, ValidationError):
            problems.append(f"{path} skipped (not a valid catalogue entry)")
            continue
        key = str(getattr(card, "id", "") or path[len(CATALOG_DIR):].removesuffix(".json"))
        out[key] = (path, card)
    return out, problems


def _skills(pin: CatalogPin, files: dict[str, bytes]) -> tuple[list[CatalogItemOut], list[str]]:
    cards, problems = _cards(files, _Skill)
    items: list[CatalogItemOut] = []
    for key in sorted(cards):
        path, s = cards[key]
        primary = normalize_category(s.category)
        upstream = (s.source.repo if s.source else "").strip()
        stars, stars_at = _pinned_stars(s.source.stars if s.source else None,
                                        s.source.stars_read_at if s.source else None)
        added_at, updated_at = _dates(pin, s.id)
        item = CatalogItemOut(
            id=s.id, kind="skills", name=_text(s.title) or s.id, description=_text(s.summary),
            # A skill copied from elsewhere is by whoever owns that repository; one with no upstream
            # was written for the catalogue.
            author=upstream.split("/", 1)[0] if upstream else "Chimera",
            license=s.license, category=primary,
            categories_secondary=normalize_secondary(s.categories_secondary, primary=primary),
            # Installing a catalogue skill lands it pending and untrusted, like any bundle.
            source=_source(pin, path), installable=True, refusal=None,
            # The stars the catalogue recorded at the pinned commit, with the day it read them; a
            # fresher reading from this machine replaces them (chimera.catalogs.stars).
            stars=stars, stars_at=stars_at,
            stars_repo=_repo(upstream) if upstream else f"{pin.owner}/{pin.repo}",
            added_at=added_at, updated_at=updated_at,
        )
        setup_free = None if s.ships_scripts is None else not s.ships_scripts
        items.append(item.mark(setup_free=setup_free, own=s.mode == "own" or not upstream))
    return items, problems


def _plugins(pin: CatalogPin, files: dict[str, bytes]) -> tuple[list[CatalogItemOut], list[str]]:
    from chimera.plugins.fetch import refusal_for
    from chimera.plugins.marketplace import parse_marketplace

    data = files.get(MARKETPLACE_FILE)
    if data is None:
        return [], [f"{MARKETPLACE_FILE} could not be read, so no plugin is listed"]
    try:
        report = parse_marketplace(_json(data))
    except ValueError:
        return [], [f"{MARKETPLACE_FILE} is not JSON"]
    cards, problems = _cards(files, _PluginCard)
    items: list[CatalogItemOut] = []
    for entry in report.plugins:
        if not entry.name:
            continue
        path, card = cards.get(entry.name, (MARKETPLACE_FILE, _PluginCard()))
        primary = normalize_category(card.category or entry.category)
        refusal = refusal_for(entry.source) or (None if entry.installable else "the entry has errors")
        own = card.origin in ("ours", "own")
        recorded = card.stars or _PluginStars()
        stars, stars_at = _pinned_stars(recorded.count, recorded.read_at)
        added_at, updated_at = _dates(pin, entry.name)
        item = CatalogItemOut(
            id=entry.name, kind="plugins",
            name=card.displayName or entry.display_name or entry.name,
            description=(entry.description or "")[:_MAX_TEXT],
            author=entry.author.name if entry.author else "",
            license=card.license, category=primary,
            categories_secondary=normalize_secondary(card.secondary, primary=primary),
            source=_source(pin, path), installable=refusal is None, refusal=refusal,
            stars=stars, stars_at=stars_at,
            stars_repo=_repo(recorded.repo) or _repo(card.upstream)
            or (f"{pin.owner}/{pin.repo}" if own else None),
            added_at=added_at, updated_at=updated_at,
        )
        items.append(item.mark(setup_free=_plugin_setup_free(card, refusal is None), own=own))
    return items, problems


def _plugin_setup_free(card: _PluginCard, installable: bool) -> bool | None:
    """Instructions only: no MCP server, no hook, no script, by the card's components, or None."""
    if not installable:
        return False
    comp = card.components
    if comp is None or comp.hooks is None or comp.script_files is None:
        return None
    return not comp.mcp_servers and not comp.hooks and comp.script_files == 0


def parse_catalog(
    kind: CatalogKind, pin: CatalogPin, files: dict[str, bytes]
) -> tuple[list[CatalogItemOut], list[str]]:
    """The items of one catalogue from its verified files, and a line per entry skipped."""
    if kind == "connectors":
        return _connectors(pin, files)
    if kind == "skills":
        return _skills(pin, files)
    return _plugins(pin, files)


__all__ = ["CATALOG_DIR", "CONNECTORS_FILE", "MARKETPLACE_FILE", "parse_catalog"]
