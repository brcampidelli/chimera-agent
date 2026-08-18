"""The curated library existed; nothing could reach it.

`tests/test_skill_library.py` checks that the twenty-three cards in `skills/` are well-formed. It
passed for months while no user could see one. The library was reachable by exactly one route —
`chimera skills-import skills/<name>`, a repo-relative path typed by someone with a checkout — and
`packages = ["chimera"]` meant an installed copy had no `skills/` directory for that path to name.
The desktop app never mentioned the library at all.

So these tests are about the *plumbing*, not the content: that the loader finds the library where a
wheel puts it and where a checkout puts it, that a name arriving from an HTTP path cannot read
outside it, and that the two new surfaces (CLI import by name, the three library routes) actually
return the cards rather than an empty list.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from chimera.skills import library

CARD = """---
name: {name}
description: {desc}
version: 0.1.0
kind: pattern
stage: verify
topic: software-dev
triggers: [a trigger phrase]
provenance: clean
status: active
license: Apache-2.0
---

## Trigger
When it applies.

## Do
The procedure.

## Avoid
The failure.

## Check
The observable.

## Risk
The cost.
"""


def write_card(root: Path, name: str, desc: str = "a curated card") -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(CARD.format(name=name, desc=desc), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _fresh_root() -> Any:
    """`library_root` is cached for the process; a test that moves it must not leak into the next."""
    library.library_root.cache_clear()
    yield
    library.library_root.cache_clear()


def test_the_shipped_library_is_visible_from_the_package() -> None:
    """The claim the whole item rests on: importable code can see the cards.

    Not a tautology over the fixtures below — this reads the REAL `skills/` directory through the
    same resolution an installed copy uses, which is the thing that was missing.
    """
    names = library.card_names()
    assert len(names) >= 10, f"only {len(names)} curated cards found — the library did not resolve"
    assert "verify-before-claiming" in names
    card = library.load_card("verify-before-claiming")
    assert card is not None
    assert card.manifest.description.strip()
    assert "## Check" in card.instructions, "the body must arrive, not just the frontmatter"


def test_every_shipped_card_survives_the_import_gate() -> None:
    """Measured before this was written: the validator refused **23 of 23**.

    Its name rule was `^[a-z][a-z0-9_]{1,40}$` — snake_case, written for the names the agent mints
    for itself — and the curated library that arrived later is kebab-case, with a longest name of 57
    characters. So `chimera skills-import skills/verify-before-claiming`, the one line the README
    offers for using the library, printed "Refused" and exited 0 for every card in it.

    `tests/test_skill_library.py` could not catch this: it checks that a card is well-formed, which
    every one of them was. Nothing checked that a well-formed card could get *in*.
    """
    from chimera.governance import SkillValidator
    from chimera.skills.skill_md import to_learned

    validator = SkillValidator()
    refused = []
    cards = library.load_library()
    for card in cards:
        verdict = validator.validate(to_learned(card).to_dict())
        if not verdict.accepted:
            refused.append(f"{card.manifest.name}: {'; '.join(verdict.reasons)}")

    assert cards, "no cards loaded — this would pass vacuously"
    assert refused == [], f"{len(refused)}/{len(cards)} shipped cards cannot be imported"


def test_the_packaged_copy_wins_over_the_source_tree(tmp_path: Path, monkeypatch: Any) -> None:
    """A wheel carries `chimera/_skill_library`; a checkout has `../skills`. Both can be present.

    They are present together in exactly the case that matters — a developer with a checkout who
    also has a built copy in the tree — and the packaged one is what a released build would use, so
    reading the checkout there would test something no user runs.
    """
    package = tmp_path / "chimera"
    (package / "skills").mkdir(parents=True)
    write_card(package / "_skill_library", "from-the-wheel")
    write_card(tmp_path / "skills", "from-the-checkout")
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(library, "__file__", str(package / "skills" / "library.py"))

    assert library.card_names() == ["from-the-wheel"]


def test_a_bare_site_packages_neighbour_is_not_mistaken_for_the_library(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The source fallback requires a sibling `pyproject.toml`, and this is why.

    `chimera/` installed into site-packages has `site-packages/` as its parent. An unrelated
    distribution shipping a top-level `skills/` there would otherwise have had its markdown parsed
    and served as this project's official, human-reviewed advice.
    """
    package = tmp_path / "chimera"
    (package / "skills").mkdir(parents=True)
    write_card(tmp_path / "skills", "somebody-elses-card")
    monkeypatch.setattr(library, "__file__", str(package / "skills" / "library.py"))

    assert library.library_root() is None
    assert library.card_names() == []


def test_a_name_from_the_wire_cannot_read_outside_the_library(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """`name` is an API path parameter and a CLI argument, so it is matched against the directory
    listing rather than joined onto a path.

    The first version of this test parametrized `../../../etc` and `/etc/passwd` against the real
    library and asserted `None`. It passed against a deliberately naive `root / name / "SKILL.md"` —
    because none of those names reach a file that exists, so both implementations returned `None`
    for the same uninteresting reason. A traversal test with nothing to traverse *to* asserts
    nothing. So a card is planted outside the library here, and the control below proves it is
    genuinely readable at its real address before any verdict is drawn from a `None`.
    """
    package = tmp_path / "chimera"
    (package / "skills").mkdir(parents=True)
    root = package / "_skill_library"
    write_card(root, "legitimate")
    outside = tmp_path / "outside"
    planted = write_card(outside, "planted", desc="not ours")
    monkeypatch.setattr(library, "__file__", str(package / "skills" / "library.py"))

    assert planted.is_file(), "the control: the file the names below aim at must actually exist"
    assert library.load_card("legitimate") is not None, "the loader must work at all"

    for name in [
        "../outside/planted",  # the relative escape
        str(outside / "planted"),  # the absolute one, which contains no dots at all
        "legitimate/../../outside/planted",  # starting from a name that IS in the library
        "..",
        "",
    ]:
        assert library.load_card(name) is None, f"{name!r} escaped the library"


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges on Windows")
def test_a_symlinked_card_is_refused(tmp_path: Path, monkeypatch: Any) -> None:
    """The library is a directory on the user's disk, and on an installed copy it is writable.

    A card directory symlinked at something outside it reads that file with a name that passes every
    lexical check — the same shape the migration importer already guards against when it stamps
    imported cards as tainted.
    """
    package = tmp_path / "chimera"
    (package / "skills").mkdir(parents=True)
    root = package / "_skill_library"
    root.mkdir()
    write_card(tmp_path / "elsewhere", "planted")
    (root / "planted").symlink_to(tmp_path / "elsewhere" / "planted", target_is_directory=True)
    monkeypatch.setattr(library, "__file__", str(package / "skills" / "library.py"))

    assert library.load_card("planted") is None


def test_one_unreadable_card_does_not_empty_the_list(tmp_path: Path, monkeypatch: Any) -> None:
    """A directory with a SKILL.md that is not a file (someone made it a directory) must cost one
    card, not the library. An empty list reads as "this build has no skills"."""
    package = tmp_path / "chimera"
    (package / "skills").mkdir(parents=True)
    root = package / "_skill_library"
    write_card(root, "good")
    (root / "broken" / "SKILL.md").mkdir(parents=True)
    monkeypatch.setattr(library, "__file__", str(package / "skills" / "library.py"))

    assert [c.manifest.name for c in library.load_library()] == ["good"]


# --- the surfaces -------------------------------------------------------------------------------

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402


def _client(monkeypatch: Any, tmp_path: Path) -> TestClient:
    from chimera.api import build_api_app
    from chimera.config import Settings, get_settings
    from chimera.interface import ChatSession

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    get_settings.cache_clear()
    return TestClient(build_api_app(lambda: ChatSession(object()), settings=Settings()))


def test_the_app_can_list_the_library(monkeypatch: Any, tmp_path: Path) -> None:
    from chimera.config import get_settings

    cards = _client(monkeypatch, tmp_path).get("/api/skills/library").json()
    get_settings.cache_clear()

    assert len(cards) >= 10
    one = next(c for c in cards if c["name"] == "verify-before-claiming")
    assert one["stage"] == "verify" and one["triggers"]
    # Metadata only: the list draws one line per card, and twenty-three bodies is a quarter of a
    # megabyte spent on text nothing on screen is showing yet.
    assert one["body"] == ""
    assert one["imported"] is False


def test_a_card_reads_and_then_imports(monkeypatch: Any, tmp_path: Path) -> None:
    """The route the screen needs to be more than a display case.

    Listing cards nobody can act on is the Kanban board's old problem in a new place: every route a
    read, so the screen shows the work and changes nothing.
    """
    from chimera.config import get_settings
    from chimera.evolution import SkillStore

    client = _client(monkeypatch, tmp_path)
    detail = client.get("/api/skills/library/verify-before-claiming").json()
    assert "## Check" in detail["body"]

    posted = client.post("/api/skills/library/verify-before-claiming/import").json()
    assert posted == {"imported": True, "name": "verify-before-claiming", "status": "active"}
    store = SkillStore(tmp_path / "home" / "skills.json")
    assert "verify-before-claiming" in store.names()
    # And the list now says so, so the button can stop offering an import that already happened.
    listed = client.get("/api/skills/library").json()
    assert next(c for c in listed if c["name"] == "verify-before-claiming")["imported"] is True
    get_settings.cache_clear()


def test_an_unknown_card_is_a_404_on_both_routes(monkeypatch: Any, tmp_path: Path) -> None:
    from chimera.config import get_settings

    client = _client(monkeypatch, tmp_path)
    assert client.get("/api/skills/library/no-such-card").status_code == 404
    assert client.post("/api/skills/library/no-such-card/import").status_code == 404
    get_settings.cache_clear()


def test_the_cli_imports_a_curated_card_by_its_bare_name(monkeypatch: Any, tmp_path: Path) -> None:
    """`chimera skills-import skills/<name>` is the line in the README, and it names a path that
    exists only in a checkout. The bare name is what an installed user can actually type."""
    from typer.testing import CliRunner

    from chimera.cli.main import app
    from chimera.config import get_settings
    from chimera.evolution import SkillStore

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["skills-import", "verify-before-claiming"])

    assert result.exit_code == 0, result.output
    assert "verify-before-claiming" in SkillStore(tmp_path / "skills.json").names()
    get_settings.cache_clear()


def test_the_cli_lists_the_library(monkeypatch: Any, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from chimera.cli.main import app
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "200")  # rich truncates to the terminal width, and CI's is narrow
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["skills-library"])

    assert result.exit_code == 0, result.output
    assert "verify-before-claiming" in result.output
    get_settings.cache_clear()
