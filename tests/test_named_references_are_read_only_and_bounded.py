"""Named owner references are a read-only path capability, not another workspace."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.tools.edit import ApplyPatchTool, EditBatchTool, EditFileTool
from chimera.tools.files import ListDirTool, ReadFileTool, WriteFileTool
from chimera.tools.references import (
    REFERENCE_SURFACES,
    bind_references,
    render_reference_guidance,
    save_references,
)
from chimera.tools.registry import ToolRegistry
from chimera.tools.search import GlobTool, GrepTool
from chimera.tools.workspace import PathEscapesWorkspaceError


@pytest.fixture
def layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    """``(workspace, reference, home)`` with one declared, described reference ``@docs``."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    reference = tmp_path / "shared"
    reference.mkdir()
    (reference / "note.txt").write_text("needle\n", encoding="utf-8")
    home = tmp_path / "home"
    save_references(home, {"docs": {"path": str(reference), "description": "Shared notes"}})
    return workspace, reference, home


def _bound(root: Path, home: Path) -> tuple[ReadFileTool, ListDirTool, GrepTool, GlobTool]:
    registry = ToolRegistry()
    tools = (ReadFileTool(root), ListDirTool(root), GrepTool(root), GlobTool(root))
    for tool in tools:
        registry.register(tool)
    bind_references(registry, home)
    return tools


def _symlink(link: Path, target: Path) -> None:
    """Create ``link`` or skip: Windows without developer mode or the privilege refuses it."""
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation is unavailable on this machine: {exc}")


def test_named_reference_read_search_list_and_glob_work(layout: tuple[Path, Path, Path]) -> None:
    workspace, _reference, home = layout
    read, list_dir, grep, glob = _bound(workspace, home)

    assert read.run(path="@docs/note.txt") == "needle\n"
    assert "note.txt" in list_dir.run(path="@docs")
    assert "note.txt" in list_dir.run(path="@docs/")
    assert "@docs/note.txt:1: needle" in grep.run(pattern="needle", path="@docs")
    assert glob.run(pattern="@docs/**/*.txt") == "@docs/note.txt"


def test_traversal_and_a_sibling_prefix_cannot_leave_the_reference(
    layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    workspace, _reference, home = layout
    read, list_dir, grep, glob = _bound(workspace, home)
    (tmp_path / "outside.txt").write_text("not readable", encoding="utf-8")
    # `shared-secret` starts with the reference's own name: a string-prefix containment check
    # (`str(candidate).startswith(str(root))`) would let it through.
    sibling = tmp_path / "shared-secret"
    sibling.mkdir()
    (sibling / "note.txt").write_text("sibling secret", encoding="utf-8")

    for path in ("@docs/../outside.txt", "@docs/../../outside.txt", "@docs/../shared-secret/note.txt"):
        with pytest.raises(PathEscapesWorkspaceError):
            read.run(path=path)
    with pytest.raises(PathEscapesWorkspaceError):
        list_dir.run(path="@docs/../shared-secret")
    with pytest.raises(PathEscapesWorkspaceError):
        grep.run(pattern="secret", path="@docs/../shared-secret")
    with pytest.raises(PathEscapesWorkspaceError):
        glob.run(pattern="@docs/../shared-secret/*.txt")


def test_every_writer_refuses_a_bound_alias(layout: tuple[Path, Path, Path]) -> None:
    workspace, reference, home = layout
    registry = ToolRegistry()
    writers: tuple[Any, ...] = (
        WriteFileTool(workspace),
        EditFileTool(workspace),
        ApplyPatchTool(workspace),
        EditBatchTool(workspace),
    )
    for tool in writers:
        registry.register(tool)
    bind_references(registry, home)

    calls: dict[str, dict[str, Any]] = {
        "write_file": {"path": "@docs/note.txt", "content": "overwritten"},
        "edit_file": {"path": "@docs/note.txt", "old": "needle", "new": "changed"},
        "apply_patch": {
            "path": "@docs/note.txt",
            "patch": "<<<<<<< SEARCH\nneedle\n=======\nchanged\n>>>>>>> REPLACE",
        },
        "edit_batch": {"edits": [{"path": "@docs/note.txt", "old": "needle", "new": "changed"}]},
    }
    for tool in writers:
        with pytest.raises(PathEscapesWorkspaceError, match="read-only"):
            tool.run(**calls[tool.name])
    with pytest.raises(PathEscapesWorkspaceError):
        writers[0].run(path="@docs/new.txt", content="not allowed")
    assert (reference / "note.txt").read_text(encoding="utf-8") == "needle\n"
    assert not (reference / "new.txt").exists()
    assert not (workspace / "@docs").exists()  # refused, not quietly redirected into the project


def test_without_references_an_at_sign_file_in_the_workspace_is_an_ordinary_file(
    tmp_path: Path,
) -> None:
    """`@x` is a reference only for a tool bound with alias `x`; anywhere else it is a file name."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    writer, reader = WriteFileTool(workspace), ReadFileTool(workspace)

    assert not writer.run(path="@x/notes.md", content="mine").startswith("error")
    assert (workspace / "@x" / "notes.md").read_text(encoding="utf-8") == "mine"
    assert reader.run(path="@x/notes.md") == "mine"


def test_an_unknown_alias_is_a_workspace_path_even_on_a_bound_tool(
    layout: tuple[Path, Path, Path],
) -> None:
    workspace, _reference, home = layout
    registry = ToolRegistry()
    writer, reader = WriteFileTool(workspace), ReadFileTool(workspace)
    registry.register(writer)
    registry.register(reader)
    bind_references(registry, home)

    assert reader.run(path="@missing/note.txt").startswith("error: file not found")
    assert not writer.run(path="@other/a.txt", content="ok").startswith("error")
    assert (workspace / "@other" / "a.txt").exists()


def test_symlinks_that_leave_a_reference_are_not_read_listed_searched_or_globbed(
    layout: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    workspace, reference, home = layout
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    _symlink(reference / "escape.txt", outside / "secret.txt")
    _symlink(reference / "escape-dir", outside)
    read, list_dir, grep, glob = _bound(workspace, home)

    with pytest.raises(PathEscapesWorkspaceError):
        read.run(path="@docs/escape.txt")
    with pytest.raises(PathEscapesWorkspaceError):
        list_dir.run(path="@docs/escape-dir")
    listing = list_dir.run(path="@docs")
    assert "escape" not in listing and "note.txt" in listing
    assert "secret" not in glob.run(pattern="@docs/**/*.txt")
    assert "escape" not in glob.run(pattern="@docs/**/*.txt")
    assert "secret" not in grep.run(pattern="secret", path="@docs")


def test_list_dir_and_grep_hide_a_workspace_symlink_that_leaves_the_workspace(
    tmp_path: Path,
) -> None:
    """The same rule in the workspace: a link out is not listed (reading it is refused anyway)."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "a.py").write_text("token = 1\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("token = secret\n", encoding="utf-8")
    _symlink(workspace / "leak.txt", outside)
    # A link between two folders of the SAME project is not an escape and stays visible: the bound
    # is the workspace, not the folder being listed.
    (workspace / "lib").mkdir()
    (workspace / "lib" / "b.py").write_text("token = 2\n", encoding="utf-8")
    _symlink(workspace / "src" / "alias.py", workspace / "lib" / "b.py")

    listing = ListDirTool(workspace).run(path=".")
    assert "leak.txt" not in listing and "src/" in listing
    assert "alias.py" in ListDirTool(workspace).run(path="src")
    hits = GrepTool(workspace).run(pattern="token")
    assert "secret" not in hits and "a.py" in hits


def test_reading_the_workspace_with_a_reference_configured_does_not_taint_the_ledger(
    layout: tuple[Path, Path, Path],
) -> None:
    """A configured reference must not turn the agent's own project into external content.

    An earlier draft set ``untrusted_output`` on the four read tools whenever a reference existed,
    and the ledger then recorded every workspace read as a fetch: one line in ``references.json``
    changed the governance of a person reading their own code.
    """
    from chimera.governance.ledger import TaintLedger
    from chimera.governance.ledger_tool import ledger_registry

    workspace, _reference, home = layout
    (workspace / "project.txt").write_text("local project", encoding="utf-8")
    registry = ToolRegistry()
    for tool in (ReadFileTool(workspace), ListDirTool(workspace), GrepTool(workspace), GlobTool(workspace)):
        registry.register(tool)
    ledger = TaintLedger()
    wrapped = ledger_registry(registry, ledger)
    bind_references(wrapped, home)

    assert wrapped.run("read_file", path="project.txt") == "local project"
    wrapped.run("list_dir", path=".")
    wrapped.run("grep", pattern="local")
    wrapped.run("glob", pattern="*.txt")
    assert ledger.taint_epoch == 0
    assert not ledger.is_tainted("project.txt")
    # A reference read is project content too — recorded as the workspace read is, not as a fetch.
    # If that is ever changed on purpose, change it for `@alias` reads only, and this line with it.
    assert wrapped.run("read_file", path="@docs/note.txt") == "needle\n"
    assert ledger.taint_epoch == 0
    assert not ledger.is_tainted("@docs/note.txt")


def test_binding_finds_read_tools_inside_governance_wrappers(
    layout: tuple[Path, Path, Path],
) -> None:
    from chimera.governance.ledger import TaintLedger
    from chimera.governance.ledger_tool import ledger_registry

    workspace, _reference, home = layout
    registry = ToolRegistry()
    registry.register(ReadFileTool(workspace))
    wrapped = ledger_registry(registry, TaintLedger())
    bind_references(wrapped, home)

    bound_read = wrapped.get("read_file")
    assert bound_read.run(path="@docs/note.txt") == "needle\n"
    assert not getattr(bound_read, "untrusted_output", False)


@pytest.fixture
def _terminal_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for var in ("CHIMERA_MCP_AUTOLOAD", "CHIMERA_TOOL_DENYLIST", "CHIMERA_TOOL_ALLOWLIST", "CHIMERA_REACH"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield home
    get_settings.cache_clear()


def _innermost(registry: Any, name: str) -> Any:
    tool = registry.get(name)
    while getattr(tool, "inner", None) is not None:
        tool = tool.inner
    return tool


@pytest.mark.parametrize(("surface", "bound"), [("chat", True), ("scenarios", False), ("cron", False)])
def test_terminal_binds_references_only_on_attended_surfaces(
    _terminal_env: Path, tmp_path: Path, surface: str, bound: bool
) -> None:
    from chimera.cli.right_hand import build_right_hand

    reference = tmp_path / "shared"
    reference.mkdir()
    save_references(_terminal_env, {"docs": {"path": str(reference), "description": ""}})
    workspace = tmp_path / "project"
    workspace.mkdir()

    registry = build_right_hand(workspace, settings=get_settings(), surface=surface).registry
    assert bool(getattr(_innermost(registry, "read_file"), "reference_roots", None)) is bound
    assert ("scenarios" in REFERENCE_SURFACES) is False


def test_described_reference_is_announced_but_undescribed_is_not(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    home = tmp_path / "home"
    save_references(
        home,
        {
            "public": {"path": str(first), "description": "Published API"},
            "quiet": {"path": str(second), "description": ""},
        },
    )
    prompt = render_reference_guidance(home)
    assert "@public" in prompt and "Published API" in prompt
    assert "@quiet" not in prompt
    from chimera.tools.references import foreground_instructions

    assert "Published API" in foreground_instructions(home)


def test_references_are_saved_in_owner_home_and_loaded(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    home = tmp_path / "home"
    normalized = save_references(home, {"code": {"path": str(shared), "description": "repo"}})

    assert json.loads((home / "references.json").read_text(encoding="utf-8")) == normalized
    assert normalized["code"]["path"] == str(shared.resolve())
    with pytest.raises(ValueError):
        save_references(home, {"bad alias": {"path": str(shared)}})
    with pytest.raises(ValueError):
        save_references(home, {"relative": {"path": "shared", "description": ""}})
    # A refused payload leaves the previous declaration exactly as it was.
    assert json.loads((home / "references.json").read_text(encoding="utf-8")) == normalized


def test_a_reference_to_root_home_or_chimera_home_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chimera.tools.references import load_references

    # The data folder lives OUTSIDE the user's home here, so each rule is exercised on its own: with
    # `~/.chimera` the "holds the data folder" rule would refuse the home too and hide a missing
    # home check. (A filesystem root holds both, so its own rule is defence in depth.)
    user_home = tmp_path / "users" / "me"
    (user_home / "projects" / "ok").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    home = tmp_path / "data" / "chimera"
    (home / "memory").mkdir(parents=True)

    refused = {
        "filesystem": Path(tmp_path.anchor),
        "user_home": user_home,
        "above_home": user_home.parent,  # holds the user's home
        "chimera_home": home,
        "above_chimera_home": home.parent,
        "inside_chimera_home": home / "memory",
    }
    for alias, path in refused.items():
        with pytest.raises(ValueError, match="cannot expose"):
            save_references(home, {alias: {"path": str(path), "description": ""}})
    # A folder UNDER the home is an ordinary reference.
    assert "ok" in save_references(home, {"ok": {"path": str(user_home / "projects" / "ok")}})
    # Validated on load too: a hand-edited file cannot smuggle one of them in.
    (home / "references.json").write_text(
        json.dumps({alias: {"path": str(path)} for alias, path in refused.items()}), encoding="utf-8"
    )
    assert load_references(home) == {}


def test_glob_with_an_absolute_pattern_answers_an_error_instead_of_raising(tmp_path: Path) -> None:
    out = GlobTool(tmp_path).run(pattern=str(tmp_path / "*.txt"))
    assert out.startswith("error:") or out == "no files match"


def test_desktop_api_reads_and_writes_references_but_bridge_cannot_reach_them(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.api.bridge_routes import ROUTES
    from chimera.config import Settings

    home = tmp_path / "home"
    reference = tmp_path / "shared"
    home.mkdir()
    reference.mkdir()
    settings = Settings(CHIMERA_HOME=str(home), _env_file=None)
    client = TestClient(build_api_app(lambda: None, settings=settings))
    payload = {"references": {"docs": {"path": str(reference.resolve()), "description": "docs"}}}

    assert client.get("/api/references").json() == {"references": {}}
    response = client.put("/api/references", json=payload)
    assert response.status_code == 200
    assert response.json() == payload
    # The bridge reaches the app only through its route table: no entry may name this path, for
    # any method, under any key.
    assert ROUTES
    assert not any("/api/references" in route.path for route in ROUTES.values())

    rejected = client.put(
        "/api/references",
        json={"references": {"bad": {"path": str(tmp_path / "missing"), "description": ""}}},
    )
    assert rejected.status_code == 422
    stored = json.loads((home / "references.json").read_text(encoding="utf-8"))
    assert stored == payload["references"]  # the refused PUT replaced nothing


def test_references_api_requires_the_configured_server_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.config import Settings

    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "owner-secret")
    get_settings.cache_clear()
    try:
        home = tmp_path / "home"
        home.mkdir()
        client = TestClient(
            build_api_app(lambda: None, settings=Settings(CHIMERA_HOME=str(home), _env_file=None))
        )
        assert client.get("/api/references").status_code == 401
        assert client.get(
            "/api/references", headers={"Authorization": "Bearer owner-secret"}
        ).status_code == 200
    finally:
        get_settings.cache_clear()
        monkeypatch.delenv("CHIMERA_SERVER_TOKEN", raising=False)
