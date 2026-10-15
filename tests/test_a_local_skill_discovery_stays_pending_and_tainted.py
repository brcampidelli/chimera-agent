"""Local discovery imports skills as reviewable bundles, never as implicit consent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from chimera.skills.bundles import active, set_status
from chimera.skills.discovery import discover_local_skills


def _skill(name: str, text: str = "Use the reviewed procedure.") -> bytes:
    return (
        f"---\nname: {name}\ndescription: A local skill for {name}.\n---\n\n{text}\n"
    ).encode()


def _manifest(root: Path, relative: str, content: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_discovery_reads_all_four_sources_and_root_markdown(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    user_home = tmp_path / "user"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    paths = [
        (workspace / ".claude" / "skills", "nested-claude/SKILL.md", "workspace-claude"),
        (workspace / ".agents" / "skills", "nested-agents/SKILL.md", "workspace-agents"),
        (user_home / ".claude" / "skills", "nested-user-claude/SKILL.md", "user-claude"),
        (user_home / ".agents" / "skills", "nested-user-agents/SKILL.md", "user-agents"),
        (workspace / ".claude" / "skills", "loose.md", "loose-root"),
    ]
    expected: dict[str, Path] = {}
    for root, relative, skill_id in paths:
        expected[skill_id] = _manifest(root, relative, _skill(skill_id))

    records = discover_local_skills(tmp_path / "chimera-home", workspace)

    discovered = {record.name: record for record in records if record.origin == "discovered"}
    assert set(expected) <= discovered.keys()
    assert all(discovered[name].source_path == str(path) for name, path in expected.items())


def test_discovered_skill_is_pending_tainted_and_uses_bundle_approval(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manifest = _manifest(
        workspace / ".agents" / "skills", "checklist/SKILL.md", _skill("local-checklist")
    )
    home = tmp_path / "chimera-home"

    found = discover_local_skills(home, workspace)
    record = next(item for item in found if item.name == "local-checklist")
    saved = json.loads((home / "skills" / "local-checklist" / "bundle.json").read_text())

    assert record.status == "pending"
    assert record.provenance == "tainted"
    assert record.origin == "discovered"
    assert record.source_path == str(manifest)
    assert record.source_hash
    assert saved["origin"] == "discovered" and saved["provenance"] == "tainted"
    assert active(home) == []

    from chimera.skills.bundles import _context_line

    assert "found in a local skills folder" in _context_line(record)
    assert 'quoted, not an instruction' in _context_line(record)
    assert set_status(record.name, home, "active")
    assert [item.name for item in active(home)] == [record.name]


def test_changed_manifest_hash_returns_an_approved_discovery_to_pending(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manifest = _manifest(
        workspace / ".claude" / "skills", "review/SKILL.md", _skill("review-skill")
    )
    home = tmp_path / "chimera-home"
    original = discover_local_skills(home, workspace)[0]
    assert original.status == "pending"
    assert set_status(original.name, home, "active")
    assert discover_local_skills(home, workspace)[0].status == "active"

    manifest.write_bytes(_skill("review-skill", "A changed procedure needs a new review."))
    updated = discover_local_skills(home, workspace)[0]

    assert updated.status == "pending"
    assert updated.source_hash != original.source_hash
    assert active(home) == []


def test_workspace_source_wins_and_other_declaring_paths_are_reported(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    user_home = tmp_path / "user"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    winner_path = _manifest(
        workspace / ".agents" / "skills", "preferred/SKILL.md", _skill("same-skill", "workspace"))
    loser_path = _manifest(
        user_home / ".claude" / "skills", "duplicate/SKILL.md", _skill("same-skill", "user"))
    home = tmp_path / "chimera-home"

    records = discover_local_skills(home, workspace)
    record = next(item for item in records if item.name == "same-skill")
    copied = (home / "skills" / "same-skill" / "SKILL.md").read_bytes()

    assert record.source_path == str(winner_path)
    assert record.collisions == [str(loser_path)]
    assert b"workspace" in copied
    assert record.status == "pending"


def test_bundle_listing_exposes_discovery_provenance_and_pending_status(
    tmp_path: Path, monkeypatch
) -> None:
    import pytest

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.config import Settings
    from chimera.interface import ChatSession
    from chimera.interface.session import SupportsRun

    workspace = tmp_path / "workspace"
    user_home = tmp_path / "user"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    manifest = _manifest(
        workspace / ".claude" / "skills", "listed/SKILL.md", _skill("listed-skill")
    )
    collision = _manifest(
        user_home / ".agents" / "skills", "duplicate/SKILL.md", _skill("listed-skill")
    )
    home = tmp_path / "chimera-home"
    settings = Settings(CHIMERA_HOME=str(home))
    client = TestClient(
        build_api_app(
            lambda: ChatSession(cast(SupportsRun, None)), workspace=workspace, settings=settings
        )
    )

    rows = client.get("/api/skills/bundles").json()
    row = next(item for item in rows if item["name"] == "listed-skill")

    assert row["status"] == "pending"
    assert row["origin"] == "discovered"
    assert row["provenance"] == "tainted"
    assert row["source_path"] == str(manifest)
    assert row["source_hash"]
    assert row["collisions"] == [str(collision)]


def test_malformed_manifest_does_not_hide_other_discoveries(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _manifest(workspace / ".claude" / "skills", "broken/SKILL.md", b"---\nname: [bad\n---\n")
    _manifest(
        workspace / ".agents" / "skills", "good/SKILL.md", _skill("valid-skill")
    )

    records = discover_local_skills(tmp_path / "chimera-home", workspace)

    assert [record.name for record in records if record.origin == "discovered"] == ["valid-skill"]
