"""A path outside the project folder is a question on the desktop, and the refusal it always was
everywhere else — and the desktop starts with the kernel in `observe`.

Item 7 of the list audited on 2026-09-16. The audit found the permissions story mostly true — jail,
declared write region, posture, host-exec gate, sandbox, denylist, taint narrowing, approval cards —
with one shape the story described and the product did not have: "if it tries to save the report in
Documents or read C:\\Windows, the interface asks". On the desktop the taint ledger and, since #495,
the policy kernel put a card on the screen and wait; the workspace jail was the one boundary that
still answered *no* to a person who would have said *yes*. It now asks — only where a screen is
bound, and never the declared write region, which stays the fail-closed boundary the injection
defence stands on.

And the kernel: it shipped `off` on every surface, so with a stock install the Security screen showed
an audit log nobody was writing. The desktop now starts in `observe` when nobody chose a mode — the
log fills and the fixed signatures refuse, nothing else changes; a mode set anywhere wins.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

import pytest

from chimera.api.code_api import CodeSeams, assemble_registry
from chimera.config import Settings, get_settings
from chimera.governance import pending
from chimera.governance.approval import ApprovalAnnouncer
from chimera.providers.gateway import LLMGateway
from chimera.tools.files import ReadFileTool, WriteFileTool
from chimera.tools.workspace import BoundaryQuestion, PathEscapesWorkspaceError, resolve_for
from chimera.tools.write_region import WriteRegion

# ------------------------------------------------------------------ the resolver


def _ws(tmp_path: pathlib.Path) -> pathlib.Path:
    ws = tmp_path / "project"
    ws.mkdir(exist_ok=True)
    return ws


def test_inside_the_project_nothing_changed(tmp_path: pathlib.Path) -> None:
    tool = ReadFileTool(_ws(tmp_path))
    tool.ask_outside = lambda *_a: (_ for _ in ()).throw(AssertionError("asked for a path inside"))  # type: ignore[attr-defined]
    assert resolve_for(tool, "src/a.py", verb="read") == (_ws(tmp_path) / "src" / "a.py").resolve()


def test_without_an_approver_the_jail_answers_exactly_as_before(tmp_path: pathlib.Path) -> None:
    tool = ReadFileTool(_ws(tmp_path))
    with pytest.raises(PathEscapesWorkspaceError, match="escapes workspace"):
        resolve_for(tool, str(tmp_path / "elsewhere.txt"), verb="read")


def test_a_yes_at_the_screen_lets_the_path_through_and_the_question_names_it(
    tmp_path: pathlib.Path,
) -> None:
    outside = (tmp_path / "Documents" / "report.md").resolve()
    asked: list[Any] = []

    def yes(question: Any) -> bool:
        asked.append(question)
        return True

    tool = WriteFileTool(_ws(tmp_path))
    tool.ask_outside = yes  # type: ignore[attr-defined]

    assert resolve_for(tool, str(outside), verb="write") == outside
    assert len(asked) == 1
    q = asked[0]
    assert isinstance(q, BoundaryQuestion)
    assert q.decision == "review", "a question, never a block"
    assert q.reason.startswith(f"write outside the project folder: {outside}")
    assert str(_ws(tmp_path).resolve()) in q.reason, "the card says which project this is"
    assert q.action == f"write_file: {outside}"


def test_a_no_at_the_screen_is_a_refusal_that_says_not_to_retry(tmp_path: pathlib.Path) -> None:
    tool = ReadFileTool(_ws(tmp_path))
    tool.ask_outside = lambda *_a: False  # type: ignore[attr-defined]
    with pytest.raises(PathEscapesWorkspaceError, match="asked and refused. Do not retry"):
        resolve_for(tool, str(tmp_path / "secret.key"), verb="read")


def test_the_declared_write_region_is_not_softened_by_a_yes(tmp_path: pathlib.Path) -> None:
    """The region is the injection defence: a page that says "also update config/secrets.py" is
    refused by it outright. A person's yes to *this* path leaves the region as it was."""
    ws = _ws(tmp_path)
    (ws / "src").mkdir()
    region = WriteRegion(["src/**"], ws)
    tool = WriteFileTool(ws, write_region=region)
    asked: list[Any] = []
    tool.ask_outside = lambda q: asked.append(q) or True  # type: ignore[attr-defined]

    out = tool.run(path=str(tmp_path / "Documents" / "report.md"), content="x")

    assert len(asked) == 1, "the person was asked — and the region still had the last word"
    assert "resolves outside the workspace" in out and "refused" in out, out
    assert not (tmp_path / "Documents" / "report.md").exists()


def test_with_no_region_declared_an_approved_write_lands_where_the_person_said(
    tmp_path: pathlib.Path,
) -> None:
    """The example the story tells: "save the report in Documents" — a yes, and the file is there."""
    tool = WriteFileTool(_ws(tmp_path))
    tool.ask_outside = lambda *_a: True  # type: ignore[attr-defined]
    target = tmp_path / "Documents" / "report.md"
    target.parent.mkdir()

    out = tool.run(path=str(target), content="# report")

    assert not out.lower().startswith("error"), out
    assert target.read_text(encoding="utf-8") == "# report"


# ------------------------------------------------------------------ through the registry the Code screen builds


def _settings(tmp_path: pathlib.Path, **kw: Any) -> Settings:
    return Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_APPROVAL_WAIT="5", **kw)  # type: ignore[arg-type]


def _assemble(tmp_path: pathlib.Path, sink: Any) -> tuple[Any, Settings]:
    ws = _ws(tmp_path)
    settings = _settings(tmp_path)
    registry, _ = assemble_registry(
        CodeSeams(), ws, settings, LLMGateway(), steps=4, surface="api:turn", approval_sink=sink
    )
    return registry, settings


def test_with_a_screen_the_file_tools_carry_the_approver_and_without_one_they_do_not(
    tmp_path: pathlib.Path,
) -> None:
    with_screen, _ = _assemble(tmp_path, ApprovalAnnouncer())
    headless, _ = _assemble(tmp_path, None)

    def inner(registry: Any, name: str) -> Any:
        tool = registry.get(name)
        while not hasattr(tool, "workspace"):
            tool = getattr(tool, "_inner", None) or getattr(tool, "inner", None)
        return tool

    for name in ("read_file", "write_file", "list_dir", "edit_file"):
        assert getattr(inner(with_screen, name), "ask_outside", None) is not None, name
        assert getattr(inner(headless, name), "ask_outside", None) is None, name


def test_reading_outside_the_project_draws_the_card_and_a_yes_reads_the_file(
    tmp_path: pathlib.Path,
) -> None:
    outside = tmp_path / "notes.txt"
    outside.write_text("the note", encoding="utf-8")
    sink = ApprovalAnnouncer()
    registry, settings = _assemble(tmp_path, sink)
    asked: list[Any] = []

    def on_screen(question: Any) -> None:
        asked.append(question)
        assert pending.answer(settings.home, question.id, True)

    sink.emit = on_screen

    out = str(registry.get("read_file").run(path=str(outside)))

    assert out == "the note"
    assert len(asked) == 1
    assert "read outside the project folder" in asked[0].reason
    assert asked[0].decision == "review"


def test_a_no_on_the_card_keeps_the_file_unread(tmp_path: pathlib.Path) -> None:
    outside = tmp_path / "id_rsa"
    outside.write_text("-----BEGIN-----", encoding="utf-8")
    sink = ApprovalAnnouncer()
    registry, settings = _assemble(tmp_path, sink)
    sink.emit = lambda question: pending.answer(settings.home, question.id, False)

    with pytest.raises(PathEscapesWorkspaceError, match="asked and refused"):
        registry.get("read_file").run(path=str(outside))


def test_headless_the_jail_refuses_at_once_and_writes_no_question(tmp_path: pathlib.Path) -> None:
    outside = tmp_path / "notes.txt"
    outside.write_text("x", encoding="utf-8")
    registry, settings = _assemble(tmp_path, None)

    with pytest.raises(PathEscapesWorkspaceError, match="escapes workspace"):
        registry.get("read_file").run(path=str(outside))
    assert pending.pending(settings.home) == [], "a question was written for nobody"


# ------------------------------------------------------------------ the desktop starts in observe


def test_the_desktop_starts_the_kernel_in_observe_when_nobody_chose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chimera.cli.main import _kernel_observes_unless_told_otherwise

    monkeypatch.setenv("CHIMERA_GOVERNANCE", "")  # own the name; the guard below restores it
    monkeypatch.delenv("CHIMERA_GOVERNANCE")
    get_settings.cache_clear()
    assert get_settings().governance_mode == "off", "precondition: the package default is still off"

    _kernel_observes_unless_told_otherwise()

    assert os.environ["CHIMERA_GOVERNANCE"] == "observe"
    assert get_settings().governance_mode == "observe"


@pytest.mark.parametrize("chosen", ["off", "enforce"])
def test_a_mode_somebody_chose_is_left_alone(monkeypatch: pytest.MonkeyPatch, chosen: str) -> None:
    from chimera.cli.main import _kernel_observes_unless_told_otherwise

    monkeypatch.setenv("CHIMERA_GOVERNANCE", chosen)
    get_settings.cache_clear()

    _kernel_observes_unless_told_otherwise()

    assert os.environ["CHIMERA_GOVERNANCE"] == chosen
    assert get_settings().governance_mode == chosen


def test_the_package_default_did_not_move() -> None:
    """`chimera serve`, the CLI and the VPS keep `off`: this is the desktop's default, not the package's."""
    assert json.loads(Settings(_env_file=None).model_dump_json())["governance_mode"] == "off"
