"""Installing other people's skills — the shape on disk, and the posture around it.

A bundle is a directory of somebody else's instructions and somebody else's scripts, fetched from
the internet on request. Most of what is worth testing here is therefore not "does the download
work" but "what does it refuse, and what does it refuse to claim".

The network is stubbed throughout: these run in CI, and a test that reaches GitHub would be
testing GitHub.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.skills import bundles
from chimera.skills.bundles import BundleError, InstalledBundle, installed, remove, set_status
from chimera.skills.catalog import CATALOG, CatalogEntry, Portability, find, license_is_permissive


class _Entry:
    """A catalogue entry, as `install` needs it."""

    name = "demo"
    description = "A demonstration skill."
    repo = "someone/skills"
    path = "skills/demo"
    ref = "main"
    license = "MIT"


def _serve(monkeypatch: pytest.MonkeyPatch, files: dict[str, bytes], *, truncated: bool = False) -> None:
    """Stand in for the two endpoints `install` uses: one tree listing, then raw file reads."""

    def fake_get(url: str, *, accept: str = "", limit: int = 0) -> bytes:
        if "git/trees" in url:
            return json.dumps(
                {
                    "truncated": truncated,
                    "tree": [
                        {"path": name, "type": "blob", "size": len(body)}
                        for name, body in files.items()
                    ],
                }
            ).encode()
        if "commits/" in url:
            return json.dumps({"sha": "0" * 40}).encode()
        for name, body in files.items():
            if url.endswith("/" + name):
                return body
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(bundles, "_get", fake_get)


# --- what it refuses ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["../escape", "/etc/passwd", "a/../../b", ""])
def test_a_file_that_would_land_outside_the_skill_is_refused(name: str, tmp_path: Path) -> None:
    # The name comes from a server. Treating a name from a server as a path is how an archive
    # writes somewhere it was never given permission to write.
    with pytest.raises(BundleError):
        bundles._safe_target(tmp_path, name)


def test_only_github_over_https_is_fetched_from(monkeypatch: pytest.MonkeyPatch) -> None:
    for url in ("http://api.github.com/x", "https://evil.example/x", "file:///etc/passwd"):
        with pytest.raises(BundleError, match="refusing to fetch"):
            bundles._get(url)
    # "Install a skill" must not quietly become "fetch and unpack whatever this address serves".


def test_a_truncated_listing_stops_the_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _serve(monkeypatch, {"SKILL.md": b"---\nname: demo\n---\n"}, truncated=True)

    # Installing PART of a skill is worse than not installing it: the files that did arrive read
    # as a complete skill, and the instructions reference the ones that did not.
    with pytest.raises(BundleError, match="truncated"):
        bundles.install(_Entry(), tmp_path)


def test_a_directory_with_no_skill_md_is_not_a_skill(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _serve(monkeypatch, {"README.md": b"hello"})

    with pytest.raises(BundleError, match="not a skill directory"):
        bundles.install(_Entry(), tmp_path)
    # And nothing is left behind pretending to be one.
    assert not (bundles.bundles_root(tmp_path) / "demo").exists()


def test_a_failed_install_leaves_nothing_half_written(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def explode(url: str, *, accept: str = "", limit: int = 0) -> bytes:
        if "git/trees" in url:
            return json.dumps(
                {"truncated": False, "tree": [
                    {"path": "SKILL.md", "type": "blob", "size": 10},
                    {"path": "scripts/x.py", "type": "blob", "size": 10},
                ]}
            ).encode()
        if url.endswith("SKILL.md"):
            return b"---\nname: demo\n---\n"
        raise BundleError("the network went away")

    monkeypatch.setattr(bundles, "_get", explode)
    with pytest.raises(BundleError):
        bundles.install(_Entry(), tmp_path)

    # A half-downloaded skill on disk reads as installed and is not, which is the worse of the
    # two failures — it fails later, somewhere else.
    root = bundles.bundles_root(tmp_path)
    assert not (root / "demo").exists()
    assert not (root / "demo.partial").exists()


# --- what it records ---------------------------------------------------------------------------


def test_an_installed_bundle_lands_switched_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _serve(monkeypatch, {"SKILL.md": b"---\nname: demo\n---\n", "scripts/go.py": b"print(1)"})

    record = bundles.install(_Entry(), tmp_path)

    # These are instructions written by a stranger, and an instruction in the system prompt has
    # the standing of one the owner wrote. Downloading is not consenting.
    assert record.status == "pending"
    assert bundles.context_lines(tmp_path) == [], "nothing pending may reach a prompt"
    # And the scripts came too — the whole reason a card store could not hold these.
    assert (bundles.bundles_root(tmp_path) / "demo" / "scripts" / "go.py").is_file()


def test_it_records_the_commit_it_actually_fetched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _serve(monkeypatch, {"SKILL.md": b"---\nname: demo\n---\n"})

    record = bundles.install(_Entry(), tmp_path)

    # A branch name says which branch, not which bytes. "Where did this come from" has to be
    # answerable later rather than remembered.
    assert record.ref == "0" * 40
    assert record.license == "MIT"
    assert "someone/skills" in record.source


def test_the_switch_goes_both_ways(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _serve(monkeypatch, {"SKILL.md": b"---\nname: demo\n---\n"})
    bundles.install(_Entry(), tmp_path)

    assert set_status("demo", tmp_path, "active")
    assert len(bundles.context_lines(tmp_path)) == 1
    assert set_status("demo", tmp_path, "inactive")

    # Off is not uninstalled: trying several and leaving two running is the normal way to use
    # these, and making "off" mean "delete" would charge a download for every change of mind.
    assert bundles.context_lines(tmp_path) == []
    assert [b.name for b in installed(tmp_path)] == ["demo"]
    assert remove("demo", tmp_path) and installed(tmp_path) == []


def test_a_directory_with_no_record_is_still_reported(tmp_path: Path) -> None:
    (bundles.bundles_root(tmp_path) / "handmade").mkdir(parents=True)

    found = installed(tmp_path)

    # It exists, and it is in the way of installing a skill by that name. Hiding it because its
    # metadata is missing would produce "already installed" from a listing that showed nothing.
    assert [(b.name, b.status) for b in found] == [("handmade", "unknown")]


# --- the catalogue -----------------------------------------------------------------------------


def test_the_catalogue_is_pinned_to_the_tree_whose_licence_was_read() -> None:
    assert CATALOG, "the build ships no catalogue"
    # The name collision is the real hazard: `pdf`, `docx`, `xlsx` and `powerpoint` exist both
    # here under MIT and in Anthropic's repo under terms that forbid keeping a copy at all.
    # Resolving by short name would pick one of the two by luck.
    for entry in CATALOG:
        assert entry.repo == "NousResearch/hermes-agent", entry.name
        assert entry.path.startswith("skills/"), entry.name
        assert "index-cache" not in entry.path, entry.name


def test_every_entry_carries_a_licence_and_a_verdict() -> None:
    for entry in CATALOG:
        assert entry.license, f"{entry.name} has no licence recorded"
        assert license_is_permissive(entry.license), f"{entry.name} is {entry.license}"
        assert isinstance(entry.portability, Portability)


def test_a_skill_that_cannot_work_here_says_so_before_the_install_button() -> None:
    macos_only = find("imessage")
    assert macos_only is not None
    # Seventy names in one flat list would advertise seventy working features and deliver rather
    # fewer, and the person who found that out would find it out after installing.
    assert macos_only.portability is Portability.OS_LOCKED
    # Case-insensitively, because the note names the operating system for a person to read and
    # "macOS" is how that name is spelled — asserting the frontmatter's lowercase token would be
    # pinning the raw identifier we deliberately stopped showing.
    assert "macos" in macos_only.note.lower()

    heavy = find("manim-video")
    assert heavy is not None and heavy.portability is Portability.NEEDS_HEAVY
    assert heavy.note, "a heavy dependency has to say what it is"


def test_requirements_the_frontmatter_understates_are_corrected() -> None:
    art = find("ascii-art")
    assert art is not None
    # Its own metadata declares `dependencies: []` and its body asks for six binaries. The
    # metadata is the author's summary, not an inventory, and passing it on as an inventory would
    # be repeating a claim we had checked and knew to be wrong.
    assert len(art.requires) >= 6


def test_an_unknown_name_is_absent_rather_than_invented() -> None:
    assert find("no-such-skill") is None


def test_a_malformed_entry_does_not_take_the_catalogue_with_it(tmp_path: Path) -> None:
    data = {"skills": [
        {"name": "good", "repo": "r", "path": "skills/good", "portability": "native"},
        {"name": "bad", "repo": "r"},  # no path
        {"name": "worse", "repo": "r", "path": "p", "portability": "not-a-rating"},
    ]}
    target = tmp_path / "catalog.json"
    target.write_text(json.dumps(data), encoding="utf-8")

    from chimera.skills import catalog as catalog_module

    original = catalog_module._DATA
    try:
        catalog_module._DATA = target
        loaded = catalog_module._load()
    finally:
        catalog_module._DATA = original

    # One bad row must not cost the other eighty-one.
    assert [e.name for e in loaded] == ["good"]


def test_a_missing_data_file_is_an_empty_catalogue_not_a_crash(tmp_path: Path) -> None:
    from chimera.skills import catalog as catalog_module

    original = catalog_module._DATA
    try:
        catalog_module._DATA = tmp_path / "nope.json"
        assert catalog_module._load() == ()
    finally:
        catalog_module._DATA = original
    # A build that lost its data file should say it ships no catalogue, not fail on import and
    # take the whole CLI down with it.


def test_search_finds_by_name_and_by_what_it_does() -> None:
    from chimera.skills.catalog import search

    assert any(e.name == "maps" for e in search("maps"))
    assert search("", topic="apple"), "topics with entries must be searchable"
    assert search("no-such-thing-anywhere") == []


def test_the_entry_shape_is_what_install_expects() -> None:
    entry = CatalogEntry(
        name="x", description="", repo="a/b", path="skills/x", license="MIT",
        portability=Portability.NATIVE,
    )
    assert entry.homepage == "https://github.com/a/b/tree/main/skills/x"
    assert isinstance(InstalledBundle(name="x").to_dict(), dict)


def test_bundle_context_never_breaks_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_home: Any) -> Any:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(bundles, "context_lines", boom)

    from chimera.core.agent import Agent

    # Skill context is an optimisation on top of a run whose tokens are already being spent. A
    # directory that could not be listed must cost the block, never the run — the same discipline
    # the retrieval path beside it already follows.
    assert Agent._bundle_context(object()) == ""  # type: ignore[arg-type]


def test_no_entry_renders_a_python_repr_at_a_person() -> None:
    """One skill's frontmatter declares `author` as a YAML list.

    `str(["a", "b"])` is a Python repr, and it went to the screen intact: a card in the shipped
    app read `de ['kshitijk4poor', 'alt-glitch', 'purzbeats']`, brackets and quotes and all.
    """
    for entry in CATALOG:
        for field, value in (("author", entry.author), ("note", entry.note),
                             ("description", entry.description)):
            assert not value.startswith(("[", "{")), f"{entry.name}.{field} = {value!r}"


def test_a_requirement_is_not_listed_twice_under_two_names() -> None:
    # `songsee` declared `songsee` and the corrections table added `songsee (go install)`, so the
    # card asked for the same binary twice and the second one read like a separate dependency.
    for entry in CATALOG:
        bases = [r.split(" (")[0].strip().lower() for r in entry.requires]
        assert len(set(bases)) == len(bases), f"{entry.name} requires {entry.requires}"


def test_the_os_badge_does_not_contradict_the_line_under_it() -> None:
    """The badge used to say "one operating system only" and count.

    Two skills run on Linux AND macOS, so that badge sat directly above "Runs only on Linux,
    macOS". A label that can disagree with its own detail is worse than a vaguer one that cannot,
    so the badge stopped counting — this pins the DATA that made counting wrong.
    """
    multi = [e for e in CATALOG if e.portability is Portability.OS_LOCKED and "," in e.note]
    assert multi, "no multi-platform entry left — did the data change, or the classifier?"
    # The note names them; the badge must not claim a number.
    from chimera.skills import catalog as catalog_module

    assert catalog_module.Portability.OS_LOCKED.value == "os_locked"
