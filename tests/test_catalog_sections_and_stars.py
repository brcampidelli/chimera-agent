"""Plan M4: catalogue dates from the pin, GitHub stars with the date read, and sections computed here.

What each test holds:

* ``added_at`` / ``updated_at`` and the pinned commit's date come from the pin file, written by the
  bump script from git history; nothing asks git at runtime;
* stars are read in the background, cached 24 h, never on the request path; offline leaves them null
  (or the dated count the pinned catalogue recorded); the setting turns every fetch off;
* each section's rule, including "For you" being ABSENT when nothing on the machine points anywhere;
* "New" is measured from the pinned commit, so it does not drift with the wall clock.

No network: every fetch is a fake.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from chimera.catalogs import stars
from chimera.catalogs.loader import load_catalog
from chimera.catalogs.model import CatalogItemOut, CatalogOut, CatalogSourceOut
from chimera.catalogs.pins import PINS_FILE, PinFile, load_pins, parse_pins
from chimera.catalogs.sections import (
    Signal,
    compute_sections,
    local_signals,
    package_tokens,
    project_signals,
)

OWNER = "brcampidelli"
SHA = "a" * 40
PIN_AT = "2026-10-18T00:13:45Z"

SKILL_OWN = json.dumps({"id": "own", "title": "Own", "category": "code", "mode": "own",
                        "ships_scripts": False, "source": None}).encode()
SKILL_UP = json.dumps({
    "id": "upstream", "title": "Up", "category": "code", "mode": "include", "ships_scripts": True,
    "source": {"repo": "someone/skills", "stars": 120, "stars_read_at": "2026-10-17"},
}).encode()
FILES = {"catalog/own.json": SKILL_OWN, "catalog/upstream.json": SKILL_UP}


def _pins(history: list[dict[str, str]] | None = None, committed_at: str | None = PIN_AT) -> PinFile:
    return PinFile.model_validate({"format": 1, "catalogs": {"skills": {
        "owner": OWNER, "repo": "chimera-skills", "sha": SHA,
        "files": [{"path": p, "sha256": hashlib.sha256(b).hexdigest()} for p, b in FILES.items()],
        "committed_at": committed_at,
        "history": history or [],
    }}})


def _get(url: str, limit: int) -> bytes:
    for path, data in FILES.items():
        if url.endswith(f"/{SHA}/{path}"):
            return data
    raise OSError(f"404 {url}")


# --- dates from the pin -----------------------------------------------------------------------------


def test_dates_come_from_the_pins_history_and_the_commit_date_rides_along(tmp_path: Path) -> None:
    pins = _pins([{"item": "own", "path": "skills/own", "added_at": "2026-10-01T10:00:00Z",
                   "updated_at": "2026-10-12T09:00:00Z"}])
    catalog = load_catalog("skills", tmp_path, pins=pins, get=_get)
    by_id = {i.id: i for i in catalog.items}

    assert catalog.committed_at == PIN_AT
    assert (by_id["own"].added_at, by_id["own"].updated_at) == (
        "2026-10-01T10:00:00Z", "2026-10-12T09:00:00Z")
    # No history line for it: not known, not invented.
    assert (by_id["upstream"].added_at, by_id["upstream"].updated_at) == (None, None)


@pytest.mark.parametrize("entry", [
    {"item": "x", "path": "skills/x", "added_at": "2026-10-01", "updated_at": "2026-10-01T00:00:00Z"},
    {"item": "x", "path": "../x", "added_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z"},
    {"item": "x", "path": "skills/x", "added_at": "2026-10-01T00:00:00Z"},
    {"item": "x", "path": "skills/x", "added_at": "2026-10-01T00:00:00Z",
     "updated_at": "2026-10-01T00:00:00Z", "stars": "1"},
])
def test_a_malformed_history_entry_does_not_validate(entry: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        _pins([entry])


def test_the_shipped_pins_carry_the_commit_date_and_one_value_per_line() -> None:
    load_pins.cache_clear()
    pins = load_pins()
    for pin in pins.catalogs.values():
        assert pin.committed_at is not None
        assert pin.history, f"{pin.repo}: the bump recorded no item dates"
        for history in pin.history.values():
            assert history.added_at <= history.updated_at <= pin.committed_at
    for line in PINS_FILE.read_text(encoding="utf-8").splitlines():
        assert line.count('":') <= 1, f"two keys on one line: {line!r}"


def _load_bump() -> Any:
    path = Path(__file__).resolve().parents[1] / "scripts" / "bump_catalog_pins.py"
    spec = importlib.util.spec_from_file_location("bump_catalog_pins_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_bump_records_first_and_last_commit_of_each_items_own_content() -> None:
    bump = _load_bump()
    asked: list[str] = []

    def api(path: str) -> list[object]:
        asked.append(path)
        if path.endswith(f"/commits/{SHA}"):
            return [{"commit": {"committer": {"date": PIN_AT}}}]
        if "path=skills/own" in path:
            return [[{"commit": {"committer": {"date": "2026-10-10T00:00:00Z"}}},
                     {"commit": {"committer": {"date": "2026-10-01T00:00:00Z"}}}]]
        return [[]]

    tree = [{"path": "catalog/own.json", "type": "blob"}, {"path": "skills/own", "type": "tree"},
            {"path": "skills/gone", "type": "tree"}]
    entry = bump.pin_one("skills", "chimera-skills", bump.CATALOGS["skills"][1], SHA,
                         api=api, body=lambda r, s, p: SKILL_OWN, tree=tree)

    assert entry["committed_at"] == PIN_AT
    assert entry["history"] == [{"item": "own", "path": "skills/own",
                                 "added_at": "2026-10-01T00:00:00Z",
                                 "updated_at": "2026-10-10T00:00:00Z"}]
    # Dated by the content folder at the pinned commit, never by the card or a branch.
    assert any("sha=" + SHA in a and "path=skills/own" in a for a in asked)
    parsed = parse_pins(json.dumps({"format": 1, "catalogs": {"skills": entry}}))
    assert parsed.catalogs["skills"].files["catalog/own.json"] == hashlib.sha256(SKILL_OWN).hexdigest()


# --- stars ------------------------------------------------------------------------------------------


class FakeApi:
    def __init__(self, counts: dict[str, int] | None = None) -> None:
        self.calls: list[str] = []
        self.online = True
        self.counts = counts or {}

    def __call__(self, url: str, limit: int) -> bytes:
        self.calls.append(url)
        if not self.online:
            raise OSError("network is unreachable")
        repo = url.removeprefix("https://api.github.com/repos/")
        if repo not in self.counts:
            from chimera.skills.bundles import _NotFoundError

            raise _NotFoundError(f"not found at the source: {url}")
        return json.dumps({"full_name": repo, "stargazers_count": self.counts[repo]}).encode()


NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def test_stars_are_read_cached_for_a_day_and_asked_again_after(tmp_path: Path) -> None:
    api = FakeApi({"a/b": 10})
    assert stars.refresh(tmp_path, ["a/b"], get=api, now=NOW) == 1
    assert stars.cached_stars(tmp_path)["a/b"] == stars.StarReading(10, "2026-10-18T12:00:00Z")

    stars.refresh(tmp_path, ["a/b"], get=api, now=NOW + timedelta(hours=23))
    assert len(api.calls) == 1  # inside the 24 h: not asked again

    api.counts["a/b"] = 11
    stars.refresh(tmp_path, ["a/b"], get=api, now=NOW + timedelta(hours=25))
    assert len(api.calls) == 2
    assert stars.cached_stars(tmp_path)["a/b"].stars == 11


def test_offline_stores_nothing_and_backs_off(tmp_path: Path) -> None:
    api = FakeApi({"a/b": 10, "c/d": 3})
    api.online = False
    assert stars.refresh(tmp_path, ["a/b", "c/d"], get=api, now=NOW) == 0
    assert len(api.calls) == 1  # the first failure ends the refresh
    assert stars.cached_stars(tmp_path) == {}

    api.online = True
    stars.refresh(tmp_path, ["a/b"], get=api, now=NOW + timedelta(minutes=5))
    assert len(api.calls) == 1  # still inside the back-off
    stars.refresh(tmp_path, ["a/b"], get=api, now=NOW + timedelta(minutes=16))
    assert stars.cached_stars(tmp_path)["a/b"].stars == 10


def test_a_missing_repository_does_not_block_the_others(tmp_path: Path) -> None:
    api = FakeApi({"z/z": 5})
    stars.refresh(tmp_path, ["a/gone", "z/z"], get=api, now=NOW)
    assert stars.cached_stars(tmp_path) == {"z/z": stars.StarReading(5, "2026-10-18T12:00:00Z")}
    stars.refresh(tmp_path, ["a/gone", "z/z"], get=api, now=NOW + timedelta(hours=1))
    assert len(api.calls) == 2  # the miss is remembered for the day too


def test_only_owner_slash_repo_names_are_ever_fetched(tmp_path: Path) -> None:
    api = FakeApi({"a/b": 1})
    stars.refresh(tmp_path, ["https://evil.test/x", "a/b/../c", "../x", "a/b"], get=api, now=NOW)
    assert api.calls == ["https://api.github.com/repos/a/b"]


def test_the_batch_is_bounded(tmp_path: Path) -> None:
    repos = [f"o/r{n}" for n in range(stars.REFRESH_BATCH + 5)]
    api = FakeApi(dict.fromkeys(repos, 1))
    stars.refresh(tmp_path, repos, get=api, now=NOW)
    assert len(api.calls) == stars.REFRESH_BATCH


def _item(id_: str, **extra: Any) -> CatalogItemOut:
    fields: dict[str, Any] = {
        "id": id_, "kind": "skills", "name": id_, "category": "code", "installable": True,
        "source": CatalogSourceOut(owner=OWNER, repo="chimera-skills", sha=SHA, path=f"catalog/{id_}.json"),
    }
    fields.update(extra)
    return CatalogItemOut(**fields)


def _catalog(*items: CatalogItemOut, committed_at: str | None = PIN_AT) -> CatalogOut:
    return CatalogOut(kind="skills", repo=f"{OWNER}/chimera-skills", sha=SHA, items=list(items),
                      available=True, committed_at=committed_at)


def test_a_fresher_reading_replaces_the_pinned_count_and_an_older_one_does_not(tmp_path: Path) -> None:
    catalog = _catalog(
        _item("x", stars=100, stars_at="2026-10-17", stars_repo="a/b"),
        _item("y", stars=100, stars_at="2026-10-20", stars_repo="a/b"),
        _item("z", stars_repo="c/d"),
    )
    stars.apply_stars(catalog, {"a/b": stars.StarReading(150, "2026-10-18T12:00:00Z")})
    by_id = {i.id: i for i in catalog.items}
    assert (by_id["x"].stars, by_id["x"].stars_at) == (150, "2026-10-18T12:00:00Z")
    assert (by_id["y"].stars, by_id["y"].stars_at) == (100, "2026-10-20")
    assert (by_id["z"].stars, by_id["z"].stars_at) == (None, None)  # no data is null, not zero


def test_the_off_switch_fetches_nothing_and_still_reads_the_cache(tmp_path: Path) -> None:
    api = FakeApi({"a/b": 7})
    stars.refresh(tmp_path, ["a/b"], get=api, now=datetime.now(UTC) - timedelta(days=3))
    api.calls.clear()
    catalog = _catalog(_item("x", stars_repo="a/b"))
    stars.with_stars(catalog, tmp_path, enabled=False, get=api)
    assert api.calls == []
    assert catalog.items[0].stars == 7  # the old reading, with its own date beside it


def test_the_request_never_waits_on_the_network(tmp_path: Path) -> None:
    release = threading.Event()
    started = threading.Event()

    def slow(url: str, limit: int) -> bytes:
        started.set()
        release.wait(5)
        raise OSError("timed out")

    catalog = _catalog(_item("x", stars_repo="a/b"))
    stars.with_stars(catalog, tmp_path, enabled=True, get=slow)
    # Returned with no reading while the fetch is still hanging on its own thread.
    assert catalog.items[0].stars is None
    assert started.wait(5)
    release.set()


def test_the_pinned_catalogue_count_is_shown_only_with_its_date(tmp_path: Path) -> None:
    catalog = load_catalog("skills", tmp_path, pins=_pins(), get=_get)
    by_id = {i.id: i for i in catalog.items}
    assert (by_id["upstream"].stars, by_id["upstream"].stars_at, by_id["upstream"].stars_repo) == (
        120, "2026-10-17", "someone/skills")
    # Our own skill's stars are the catalogue repository's, and nothing recorded them yet.
    assert (by_id["own"].stars, by_id["own"].stars_repo) == (None, f"{OWNER}/chimera-skills")


# --- sections ---------------------------------------------------------------------------------------


def _ids(catalog: CatalogOut, section: str) -> list[str]:
    for s in catalog.sections:
        if s.id == section:
            return [row.id for row in s.items]
    return []


def test_works_without_setup_and_library_follow_the_catalogue_record(tmp_path: Path) -> None:
    catalog = load_catalog("skills", tmp_path, pins=_pins(), get=_get)
    catalog.sections = compute_sections(catalog, [])
    assert _ids(catalog, "no_setup") == ["own"]  # upstream ships scripts
    assert _ids(catalog, "library") == ["own"]


def test_connectors_with_a_secret_or_that_execute_need_setup() -> None:
    def conn(id_: str, setup_free: bool | None) -> CatalogItemOut:
        return _item(id_, kind="connectors").mark(setup_free=setup_free, own=False)

    catalog = _catalog(conn("plain", True), conn("oauth", False), conn("unknown", None))
    catalog.sections = compute_sections(catalog, [])
    assert _ids(catalog, "no_setup") == ["plain"]


def test_for_you_is_absent_without_a_local_signal(tmp_path: Path) -> None:
    catalog = _catalog(_item("supabase"), _item("stripe"))
    assert all(s.id != "for_you" for s in compute_sections(catalog, []))
    # An empty CHIMERA_HOME has no servers, no skills, no projects: still absent.
    assert all(s.id != "for_you" for s in compute_sections(catalog, local_signals(tmp_path)))


def test_for_you_names_the_reason_and_skips_what_is_already_here() -> None:
    catalog = _catalog(_item("supabase"), _item("stripe"), _item("other"),
                       _item("github", kind="connectors"))
    signals = [
        Signal("supabase", "the MCP server “supabase” is configured here", covers_kind="connectors"),
        Signal("github", "the MCP server “github” is configured here", covers_kind="connectors"),
        Signal("stripe", "your project “shop” uses stripe (package.json)"),
    ]
    sections = {s.id: s for s in compute_sections(catalog, signals)}
    rows = {row.id: row.reason for row in sections["for_you"].items}
    assert set(rows) == {"supabase", "stripe"}  # the github connector is the signal itself
    assert rows["supabase"] == "Because the MCP server “supabase” is configured here."
    assert "shop" in rows["stripe"]


def test_project_manifests_are_read_locally_into_signals(tmp_path: Path) -> None:
    project = tmp_path / "shop"
    project.mkdir()
    (project / "package.json").write_text(json.dumps(
        {"dependencies": {"@supabase/supabase-js": "^2"}, "devDependencies": {"stripe": "1"}}))
    (project / "requirements.txt").write_text("# comment\nsentry-sdk==2.0\n-r other.txt\n")
    (project / "pyproject.toml").write_text('[project]\ndependencies = ["resend>=1"]\n')
    tokens = {s.token for s in project_signals([("shop", project)])}
    assert {"supabase", "supabase-js", "stripe", "sentry", "resend"} <= tokens
    assert package_tokens("@playwright/test") == {"playwright", "test"}


def test_the_local_signals_read_the_project_registry(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "package.json").write_text(json.dumps({"dependencies": {"stripe": "1"}}))
    (tmp_path / "code_projects.json").write_text(json.dumps(
        {"projects": [{"path": str(project), "alias": "Loja"}]}))
    reasons = [s.reason for s in local_signals(tmp_path) if s.token == "stripe"]
    assert reasons == ["your project “Loja” uses stripe (package.json)"]


def test_new_and_updated_are_measured_from_the_pinned_commit_not_today() -> None:
    old_pin = "2020-03-31T00:00:00Z"  # years before any wall clock running this test
    catalog = _catalog(
        _item("fresh", added_at="2020-03-10T00:00:00Z", updated_at="2020-03-10T00:00:00Z"),
        _item("stale", added_at="2019-01-01T00:00:00Z", updated_at="2019-01-02T00:00:00Z"),
        _item("touched", added_at="2019-06-01T00:00:00Z", updated_at="2020-03-20T00:00:00Z"),
        _item("sameday", added_at="2020-03-20T00:00:00Z", updated_at="2020-03-20T05:00:00Z"),
        _item("undated"),
        committed_at=old_pin,
    )
    catalog.sections = compute_sections(catalog, [])
    assert _ids(catalog, "new") == ["fresh", "sameday"]
    assert _ids(catalog, "updated") == ["touched"]  # an edit on the day it was added is not one
    new = next(s for s in catalog.sections if s.id == "new")
    assert "2020-03-31" in new.items[0].reason


def test_without_a_pinned_commit_date_there_is_no_new_or_updated() -> None:
    catalog = _catalog(_item("x", added_at="2026-10-17T00:00:00Z", updated_at="2026-10-17T00:00:00Z"),
                       committed_at=None)
    assert {s.id for s in compute_sections(catalog, [])} <= {"no_setup", "library", "for_you"}


def test_no_section_is_ever_empty() -> None:
    catalog = _catalog(_item("x"))
    assert all(s.items for s in compute_sections(catalog, []))
    assert compute_sections(catalog, []) == []
