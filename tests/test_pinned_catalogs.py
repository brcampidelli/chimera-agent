"""The three public catalogues are read from pinned commits only, and never change under the owner.

Plan M3. What each test holds:

* only a tuple ``(owner, repo, sha, path)`` the shipped pin file names is fetched, from the URL built
  out of that tuple — anything else is refused before a request is made;
* bytes whose sha256 is not the pinned one are refused: not parsed, not cached;
* verified bytes are cached by commit, so the catalogue reads offline; with nothing cached and no
  network the catalogue is empty with a stated reason, and no exception reaches the route;
* a response over the size cap is refused, not truncated;
* an unknown category lands in ``other``;
* the route's shape, its place on the desktop bridge (a read), and no usage-count field anywhere.

No network: every fetch goes through a fake.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api.bridge_routes import OWNER_DECISION_ROUTES, ROUTES
from chimera.catalogs import loader
from chimera.catalogs.categories import CATEGORY_IDS, OTHER, normalize_category
from chimera.catalogs.loader import CatalogRefused, CatalogUnreachable, fetch_pinned, load_catalog
from chimera.catalogs.pins import (
    KINDS,
    MAX_CATALOG_FILE_BYTES,
    PINS_FILE,
    PinFile,
    load_pins,
    parse_pins,
)
from chimera.config import get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun

OWNER = "brcampidelli"
SKILLS_SHA = "1" * 40
PLUGINS_SHA = "2" * 40
CONNECTORS_SHA = "3" * 40


def _skill(id_: str, category: str, **extra: Any) -> bytes:
    card = {
        "id": id_,
        "title": {"pt-BR": f"{id_} (pt)", "en": f"{id_} (en)"},
        "summary": {"pt-BR": "resumo", "en": "summary"},
        "category": category,
        "categories_secondary": ["security", "astrology", category],
        "license": "Apache-2.0",
        "source": None,
        "installs": 999,  # an unknown field in a catalogue is ignored, never shown
        **extra,
    }
    return json.dumps(card).encode()


MARKETPLACE = json.dumps({
    "name": "chimera-plugins",
    "owner": {"name": "Bruno"},
    "plugins": [
        {"name": "ours", "source": "./plugins/ours", "description": "Ours.",
         "author": {"name": "Chimera"}, "category": "code"},
        {"name": "floating", "source": {"source": "github", "repo": "someone/thing"},
         "description": "Not pinned.", "author": {"name": "Someone"}},
    ],
}).encode()

CONNECTORS = json.dumps({
    "format": 1,
    "connectors": [
        {"id": "remote", "name": "Remote", "vendor": "Vendor", "kind": "mcp", "transport": "http",
         "http": {"url": "https://mcp.example.com/mcp"}, "license": None, "notes": "A server.",
         "categories": {"primary": "databases", "secondary": ["code"]},
         "auth": {"method": "oauth", "client_restriction": "none"}},
        {"id": "api", "name": "An API", "vendor": "Vendor", "kind": "openapi", "transport": "http",
         "categories": {"primary": "nonsense", "secondary": []}},
        {"id": "closed", "name": "Closed", "vendor": "Vendor", "kind": "mcp", "transport": "http",
         "http": {"url": "https://mcp.closed.example/mcp"}, "categories": {"primary": "design-media"},
         "auth": {"method": "oauth", "client_restriction": "allowlist"}},
        {"name": "no id — skipped"},
    ],
}).encode()

FILES: dict[tuple[str, str], bytes] = {
    ("chimera-skills", "catalog/alpha.json"): _skill("alpha", "databases"),
    ("chimera-skills", "catalog/beta.json"): _skill("beta", "astrology"),
    ("chimera-plugins", ".claude-plugin/marketplace.json"): MARKETPLACE,
    ("chimera-plugins", "catalog/ours.json"): json.dumps(
        {"id": "ours", "displayName": "Our plugin", "category": "pull-requests",
         "secondary": ["code"], "license": "Apache-2.0"}
    ).encode(),
    ("chimera-connectors", "dist/catalog.json"): CONNECTORS,
}
SHAS = {"chimera-skills": SKILLS_SHA, "chimera-plugins": PLUGINS_SHA,
        "chimera-connectors": CONNECTORS_SHA}
KIND_REPO = {"skills": "chimera-skills", "plugins": "chimera-plugins",
             "connectors": "chimera-connectors"}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pins() -> PinFile:
    catalogs: dict[str, Any] = {}
    for kind, repo in KIND_REPO.items():
        catalogs[kind] = {
            "owner": OWNER, "repo": repo, "sha": SHAS[repo],
            "files": {path: _digest(data) for (r, path), data in FILES.items() if r == repo},
        }
    return PinFile.model_validate({"format": 1, "catalogs": catalogs})


def _url(repo: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{OWNER}/{repo}/{SHAS[repo]}/{path}"


class FakeGitHub:
    """Serves ``FILES`` at their raw URLs, records every request, and can be switched off."""

    def __init__(self, override: dict[str, bytes] | None = None) -> None:
        self.calls: list[str] = []
        self.online = True
        self.override = override or {}

    def __call__(self, url: str, limit: int) -> bytes:
        self.calls.append(url)
        if not self.online:
            raise OSError("network is unreachable")
        if url in self.override:
            return self.override[url]
        for (repo, path), data in FILES.items():
            if url == _url(repo, path):
                return data
        raise OSError(f"404 {url}")


@pytest.fixture
def pins() -> PinFile:
    return _pins()


# --- the shipped pin file ------------------------------------------------------------------------------


def test_the_shipped_pins_name_the_three_public_repositories_at_full_commits() -> None:
    shipped = parse_pins(PINS_FILE.read_text(encoding="utf-8"))
    assert set(shipped.catalogs) == set(KINDS)
    assert {pin.repo for pin in shipped.catalogs.values()} == {
        "chimera-connectors", "chimera-skills", "chimera-plugins"
    }
    for pin in shipped.catalogs.values():
        assert pin.owner == OWNER
        assert len(pin.sha) == 40
        assert pin.files and all(len(d) == 64 for d in pin.files.values())
    assert "dist/catalog.json" in shipped.catalogs["connectors"].files
    assert ".claude-plugin/marketplace.json" in shipped.catalogs["plugins"].files


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c["skills"].update(sha="main"),
        lambda c: c["skills"].update(sha="A" * 40),
        lambda c: c["skills"]["files"].update({"../escape.json": "0" * 64}),
        lambda c: c["skills"]["files"].update({"/abs.json": "0" * 64}),
        lambda c: c["skills"]["files"].update({"catalog/x.json": "not-a-hash"}),
        lambda c: c["skills"].update(owner="evil/owner"),
    ],
)
def test_a_pin_file_with_a_moving_ref_an_escape_or_a_short_hash_does_not_validate(
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    raw = json.loads(_pins().model_dump_json())
    mutate(raw["catalogs"])
    with pytest.raises(ValueError):
        parse_pins(json.dumps(raw))


def test_the_category_list_is_the_canonical_twenty_six() -> None:
    assert len(CATEGORY_IDS) == 26 == len(set(CATEGORY_IDS))
    assert CATEGORY_IDS[0] == "engineering" and CATEGORY_IDS[-1] == "ai-models-agents"
    assert OTHER not in CATEGORY_IDS
    assert normalize_category("Databases ") == "databases"
    assert normalize_category("astrology") == OTHER
    assert normalize_category(None) == OTHER


# --- fetch ---------------------------------------------------------------------------------------------


def test_a_pinned_file_is_fetched_from_its_commit_verified_and_cached(
    tmp_path: Path, pins: PinFile
) -> None:
    github = FakeGitHub()
    data = fetch_pinned(OWNER, "chimera-skills", SKILLS_SHA, "catalog/alpha.json",
                        home=tmp_path, pins=pins, get=github)

    assert data == FILES[("chimera-skills", "catalog/alpha.json")]
    assert github.calls == [_url("chimera-skills", "catalog/alpha.json")]
    cached = (tmp_path / "catalogs" / "cache" / f"{OWNER}__chimera-skills" / SKILLS_SHA / _digest(data))
    assert cached.read_bytes() == data
    # The second read is the cache's: no request.
    fetch_pinned(OWNER, "chimera-skills", SKILLS_SHA, "catalog/alpha.json",
                 home=tmp_path, pins=pins, get=github)
    assert len(github.calls) == 1


@pytest.mark.parametrize(
    ("owner", "repo", "sha", "path"),
    [
        (OWNER, "chimera-skills", SKILLS_SHA, "catalog/unlisted.json"),  # a file not in the pin
        (OWNER, "chimera-skills", "9" * 40, "catalog/alpha.json"),  # another commit
        (OWNER, "chimera-skills", PLUGINS_SHA, "catalog/alpha.json"),  # a sha of another repo
        ("someone", "chimera-skills", SKILLS_SHA, "catalog/alpha.json"),  # a fork
        (OWNER, "other-repo", SKILLS_SHA, "catalog/alpha.json"),
    ],
)
def test_an_unpinned_tuple_is_refused_before_any_request(
    tmp_path: Path, pins: PinFile, owner: str, repo: str, sha: str, path: str
) -> None:
    github = FakeGitHub()
    with pytest.raises(CatalogRefused, match="not a pinned catalogue file"):
        fetch_pinned(owner, repo, sha, path, home=tmp_path, pins=pins, get=github)
    assert github.calls == []


def test_bytes_that_are_not_the_pinned_ones_are_refused_not_parsed_and_not_cached(
    tmp_path: Path, pins: PinFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _url("chimera-skills", "catalog/alpha.json")
    github = FakeGitHub({url: _skill("alpha", "security", license="GPL-3.0")})
    with pytest.raises(CatalogRefused, match="sha256"):
        fetch_pinned(OWNER, "chimera-skills", SKILLS_SHA, "catalog/alpha.json",
                     home=tmp_path, pins=pins, get=github)
    assert not (tmp_path / "catalogs").exists() or not any(
        p.is_file() for p in (tmp_path / "catalogs").rglob("*")
    )

    parsed: list[dict[str, bytes]] = []
    from chimera.catalogs import parse

    real = parse.parse_catalog

    def spy(kind: Any, pin: Any, files: dict[str, bytes]) -> Any:
        parsed.append(dict(files))
        return real(kind, pin, files)

    monkeypatch.setattr(parse, "parse_catalog", spy)
    result = load_catalog("skills", tmp_path, pins=pins, get=github)
    # The tampered file never reached the parser; the honest one did.
    assert parsed == [{"catalog/beta.json": FILES[("chimera-skills", "catalog/beta.json")]}]
    assert [item.id for item in result.items] == ["beta"]
    assert any("refused" in p for p in result.problems)
    assert result.reason and "partial" in result.reason


def test_a_catalogue_whose_every_file_is_refused_is_empty_with_the_reason(
    tmp_path: Path, pins: PinFile
) -> None:
    github = FakeGitHub({_url("chimera-connectors", "dist/catalog.json"): b'{"connectors": []}'})
    result = load_catalog("connectors", tmp_path, pins=pins, get=github)
    assert result.items == [] and result.available is False
    assert result.reason == "every connectors catalogue file was refused; nothing was read"


def test_a_response_over_the_size_cap_is_refused(tmp_path: Path, pins: PinFile) -> None:
    url = _url("chimera-skills", "catalog/alpha.json")
    github = FakeGitHub({url: b" " * (MAX_CATALOG_FILE_BYTES + 1)})
    with pytest.raises(CatalogRefused, match="larger than"):
        fetch_pinned(OWNER, "chimera-skills", SKILLS_SHA, "catalog/alpha.json",
                     home=tmp_path, pins=pins, get=github)


def test_the_real_fetch_is_asked_for_no_more_than_the_cap(
    tmp_path: Path, pins: PinFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default fetch is the plugin installer's bounded, allowlisted GET, with the catalogue cap."""
    from chimera.skills import bundles

    seen: list[tuple[str, str, int]] = []

    def fake_get(url: str, *, accept: str = "", limit: int = 0) -> bytes:
        seen.append((url, accept, limit))
        raise bundles.BundleError(f"the response is larger than the {limit // 1024}KB limit")

    monkeypatch.setattr(bundles, "_get", fake_get)
    with pytest.raises(CatalogRefused, match="larger than"):
        fetch_pinned(OWNER, "chimera-skills", SKILLS_SHA, "catalog/alpha.json",
                     home=tmp_path, pins=pins)
    assert seen == [(_url("chimera-skills", "catalog/alpha.json"), "*/*", MAX_CATALOG_FILE_BYTES)]


def test_a_cached_file_that_no_longer_matches_is_dropped_and_fetched_again(
    tmp_path: Path, pins: PinFile
) -> None:
    github = FakeGitHub()
    args = (OWNER, "chimera-skills", SKILLS_SHA, "catalog/alpha.json")
    data = fetch_pinned(*args, home=tmp_path, pins=pins, get=github)
    cached = next(p for p in (tmp_path / "catalogs").rglob("*") if p.is_file())
    cached.write_bytes(b"edited by hand")

    assert fetch_pinned(*args, home=tmp_path, pins=pins, get=github) == data
    assert len(github.calls) == 2
    assert cached.read_bytes() == data


# --- offline -------------------------------------------------------------------------------------------


def test_offline_with_a_cache_reads_the_whole_catalogue(tmp_path: Path, pins: PinFile) -> None:
    github = FakeGitHub()
    first = load_catalog("skills", tmp_path, pins=pins, get=github)
    github.online = False
    github.calls.clear()
    again = load_catalog("skills", tmp_path, pins=pins, get=github)

    assert github.calls == []
    assert again.available and again.reason is None and again.problems == []
    assert again.model_dump() == first.model_dump()


def test_offline_without_a_cache_is_empty_with_a_reason_after_one_attempt(
    tmp_path: Path, pins: PinFile
) -> None:
    github = FakeGitHub()
    github.online = False
    result = load_catalog("skills", tmp_path, pins=pins, get=github)

    assert result.items == [] and result.available is False
    assert result.reason is not None and result.reason.startswith("offline")
    assert "brcampidelli/chimera-skills" in result.reason
    assert result.sha == SKILLS_SHA
    # The first unreachable file decides the rest: one request, not one per file.
    assert len(github.calls) == 1


def test_network_false_reads_the_cache_only(tmp_path: Path, pins: PinFile) -> None:
    with pytest.raises(CatalogUnreachable):
        fetch_pinned(OWNER, "chimera-skills", SKILLS_SHA, "catalog/alpha.json",
                     home=tmp_path, pins=pins, get=FakeGitHub(), network=False)


def test_an_unreadable_pin_file_is_an_empty_catalogue_not_an_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> PinFile:
        raise ValueError("bad pins")

    monkeypatch.setattr(loader, "load_pins", broken)
    result = load_catalog("plugins", tmp_path, get=FakeGitHub())
    assert result.available is False and result.reason and "pin file" in result.reason


# --- parsing -------------------------------------------------------------------------------------------


def test_skills_read_into_neutral_items_and_an_unknown_category_is_other(
    tmp_path: Path, pins: PinFile
) -> None:
    result = load_catalog("skills", tmp_path, pins=pins, get=FakeGitHub())
    by_id = {item.id: item for item in result.items}

    assert by_id["alpha"].category == "databases"
    assert by_id["beta"].category == OTHER
    # Secondary: the unknown one and the repeat of the primary are dropped.
    assert by_id["alpha"].categories_secondary == ["security"]
    alpha = by_id["alpha"]
    assert (alpha.name, alpha.description, alpha.author, alpha.license) == (
        "alpha (en)", "summary", "Chimera", "Apache-2.0"
    )
    assert alpha.source.model_dump() == {
        "owner": OWNER, "repo": "chimera-skills", "sha": SKILLS_SHA, "path": "catalog/alpha.json"
    }
    assert alpha.installable is True and alpha.refusal is None
    assert (alpha.stars, alpha.stars_at, alpha.added_at, alpha.updated_at) == (None,) * 4


def test_plugins_join_the_marketplace_and_the_cards_and_carry_the_installers_refusal(
    tmp_path: Path, pins: PinFile
) -> None:
    result = load_catalog("plugins", tmp_path, pins=pins, get=FakeGitHub())
    by_id = {item.id: item for item in result.items}

    ours = by_id["ours"]
    assert (ours.name, ours.category, ours.license, ours.author) == (
        "Our plugin", "pull-requests", "Apache-2.0", "Chimera"
    )
    assert ours.installable and ours.source.path == "catalog/ours.json"
    floating = by_id["floating"]
    assert floating.installable is False
    assert floating.refusal == "the source is not pinned (github, url and git-subdir need sha, an archive needs sha256)"
    assert floating.category == OTHER and floating.source.path == ".claude-plugin/marketplace.json"


def test_connectors_say_which_can_be_added_and_why_not(tmp_path: Path, pins: PinFile) -> None:
    result = load_catalog("connectors", tmp_path, pins=pins, get=FakeGitHub())
    by_id = {item.id: item for item in result.items}

    assert set(by_id) == {"remote", "api", "closed"}
    assert any("connector #3 skipped" in p for p in result.problems)
    assert by_id["remote"].installable and by_id["remote"].author == "Vendor"
    assert by_id["remote"].categories_secondary == ["code"]
    assert by_id["api"].installable is False and "OpenAPI" in (by_id["api"].refusal or "")
    assert by_id["api"].category == OTHER
    assert by_id["closed"].installable is False and "allowlist" in (by_id["closed"].refusal or "")


# --- the route -----------------------------------------------------------------------------------------

URL = "http://127.0.0.1:65012"


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "false")
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach(URL)
    return app


def test_the_route_answers_the_neutral_shape_and_the_bridge_reads_the_same(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = FakeGitHub()
    monkeypatch.setattr(loader, "load_pins", _pins)
    monkeypatch.setattr(loader, "_github_get", github)
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        res = client.get("/api/catalogs/skills")
        assert res.status_code == 200, res.text
        body = res.json()
        assert set(body) == {"kind", "repo", "sha", "items", "available", "reason", "problems"}
        assert body["repo"] == f"{OWNER}/chimera-skills" and body["sha"] == SKILLS_SHA
        assert set(body["items"][0]) == {
            "id", "kind", "name", "description", "author", "license", "category",
            "categories_secondary", "source", "installable", "refusal",
            "stars", "stars_at", "added_at", "updated_at",
        }
        assert (tmp_path / "home" / "catalogs" / "cache").is_dir()

        token = app.state.desktop_bridge.token or "no-token"
        via = client.post("/api/bridge/call", json={"route": "catalogs.read",
                                                    "params": {"kind": "skills"}},
                          headers={"Authorization": f"Bearer {token}"})
        assert via.status_code == 200, via.text
        assert via.json()["data"]["items"] == body["items"]

        assert client.get("/api/catalogs/everything").status_code == 422


def test_the_route_never_errors_when_nothing_can_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = FakeGitHub()
    github.online = False
    monkeypatch.setattr(loader, "load_pins", _pins)
    monkeypatch.setattr(loader, "_github_get", github)
    with TestClient(_app(tmp_path, monkeypatch)) as client:
        for kind in KINDS:
            res = client.get(f"/api/catalogs/{kind}")
            assert res.status_code == 200
            assert res.json()["available"] is False and res.json()["reason"]


def test_the_catalogue_on_the_bridge_is_one_read_and_nothing_that_installs() -> None:
    catalog_routes = {rid: r for rid, r in ROUTES.items() if rid.startswith("catalogs.")}
    assert set(catalog_routes) == {"catalogs.read"}
    assert catalog_routes["catalogs.read"].method == "GET"
    assert catalog_routes["catalogs.read"].tier == "operate"
    assert not any(rid.startswith("catalogs.") for rid in OWNER_DECISION_ROUTES)


def _property_names(node: Any) -> set[str]:
    if isinstance(node, dict):
        names = set(node.get("properties", {})) if isinstance(node.get("properties"), dict) else set()
        return names | {n for v in node.values() for n in _property_names(v)}
    if isinstance(node, list):
        return {n for v in node for n in _property_names(v)}
    return set()


def test_no_usage_count_field_exists_anywhere_in_the_api_schema() -> None:
    """Chimera measures no installs. A field for one would be filled with an invented number."""
    schema = build_api_app(lambda: ChatSession(cast(SupportsRun, None))).openapi()
    forbidden = {"installs", "downloads", "install_count", "download_count", "installations"}

    assert not forbidden & _property_names(schema)
    catalog_item = schema["components"]["schemas"]["CatalogItemOut"]["properties"]
    assert {"stars", "stars_at", "added_at", "updated_at"} <= set(catalog_item)


def test_the_shipped_pins_load_through_the_cached_loader() -> None:
    load_pins.cache_clear()
    assert set(load_pins().catalogs) == set(KINDS)


def test_the_shipped_pin_file_keeps_each_digest_on_its_own_line() -> None:
    """A digest beside a file name reads as a credential to the secret scanner (``sentry.json``)."""
    raw = json.loads(PINS_FILE.read_text(encoding="utf-8"))
    for pin in raw["catalogs"].values():
        assert isinstance(pin["files"], list)
        assert all(set(entry) == {"path", "sha256"} for entry in pin["files"])
    for line in PINS_FILE.read_text(encoding="utf-8").splitlines():
        assert not ('.json"' in line and len(line.split('"')) > 3 and any(len(p) == 64 for p in line.split('"')))


def test_a_file_listed_twice_does_not_validate() -> None:
    raw = json.loads(PINS_FILE.read_text(encoding="utf-8"))
    raw["catalogs"]["skills"]["files"].append(dict(raw["catalogs"]["skills"]["files"][0]))
    with pytest.raises(ValueError):
        parse_pins(json.dumps(raw))
