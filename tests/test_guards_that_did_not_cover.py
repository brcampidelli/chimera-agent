"""Four guards that existed and did not cover the case they were written for.

None of these is a missing feature. Each is a mechanism already in the codebase, already tested on
its own, and reachable by every path except one — and in three of the four, the path it missed is the
default one. That is the shape worth a file of its own: a guard nobody has to disable, because it was
never wired into the configuration everybody runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core.subagent import SubAgentTool
from chimera.tools.base import Tool
from chimera.tools.files import WriteFileTool
from chimera.tools.registry import ToolRegistry
from chimera.tools.write_region import ALWAYS_DENIED, WriteRegion, refuse_write


class _Spy(Tool):
    name = "spy"
    description = "records that it ran"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.calls = 0

    def run(self, **kwargs: Any) -> str:
        self.calls += 1
        return "ran"


class _Wrapped(Tool):
    """Stands in for a governance wrapper: same name, different behaviour."""

    def __init__(self, inner: Tool) -> None:
        self.inner = inner
        self.name = inner.name
        self.description = inner.description
        self.parameters = inner.parameters

    def run(self, **kwargs: Any) -> str:
        return f"governed: {self.inner.run(**kwargs)}"


# --- 1. the subagent drew from the registry as it was BEFORE the wrappers -----------------------


def test_a_late_bound_subagent_sees_the_wrapped_registry() -> None:
    """The hole: a caller registers the spawn tool, THEN wraps the registry and rebinds its own
    variable. The tool kept the raw object, so a subagent ran with ungoverned tools — the one path
    in the system that spawns work outside every guard its parent is under."""
    registry = ToolRegistry()
    registry.register(_Spy())
    tool = SubAgentTool(object(), lambda: registry, model="m")

    # …and now the caller wraps, exactly as `chimera solve` does.
    wrapped = ToolRegistry()
    for inner in registry.tools():
        wrapped.register(_Wrapped(inner))
    registry = wrapped

    sub = tool._build_registry(None)

    assert isinstance(sub.get("spy"), _Wrapped), "the subagent got the pre-wrapper tool"


def test_passing_the_object_still_works_for_callers_that_do_not_wrap() -> None:
    # The signature widened; it must not have narrowed. Most callers never wrap anything.
    registry = ToolRegistry()
    registry.register(_Spy())

    tool = SubAgentTool(object(), registry, model="m")

    assert tool._build_registry(None).names() == ["spy"]


def test_the_spawn_tool_is_never_granted_to_the_subagent() -> None:
    # Unchanged behaviour, asserted because the allowlist moved to being computed per call.
    registry = ToolRegistry()
    registry.register(_Spy())
    tool = SubAgentTool(object(), lambda: registry, model="m")
    registry.register(tool)

    assert "spawn_subagent" not in tool._build_registry(None).names()


# --- 2. the write region was consulted only when one was declared -------------------------------


def test_git_is_refused_with_no_region_declared(tmp_path: Path) -> None:
    """The default configuration. A denylist that only ran when someone declared a region would be
    unreachable in the case everybody runs — code that looks like a guard and never executes."""
    (tmp_path / ".git").mkdir()

    assert refuse_write(tmp_path, tmp_path / ".git" / "config", None) is not None


def test_a_declared_region_cannot_grant_it_back(tmp_path: Path) -> None:
    # A denylist a declaration can override is a suggestion.
    region = WriteRegion(["**"], tmp_path)

    assert region.allows(tmp_path / ".git" / "config") is False
    assert refuse_write(tmp_path, tmp_path / ".git" / "HEAD", region) is not None


def test_the_receipts_and_the_env_are_denied_too(tmp_path: Path) -> None:
    for denied in ALWAYS_DENIED:
        assert refuse_write(tmp_path, tmp_path / denied / "x", None) is not None, denied


def test_an_ordinary_file_is_still_allowed(tmp_path: Path) -> None:
    # The counterpart. A guard that refuses everything is not a guard, it is an outage.
    assert refuse_write(tmp_path, tmp_path / "src" / "main.py", None) is None
    assert refuse_write(tmp_path, tmp_path / "gitignore.md", None) is None, "prefix match, not path"


def test_the_writer_tool_refuses_through_the_same_gate(tmp_path: Path) -> None:
    """Tests the WIRING, not the function: the tool used to check `if region is not None`, so a
    correct helper reached from nowhere would have proved nothing."""
    (tmp_path / ".git").mkdir()
    tool = WriteFileTool(tmp_path)

    out = tool.run(path=".git/config", content="[core]")

    assert out.startswith("error:")
    assert not (tmp_path / ".git" / "config").exists()


# --- 3. `evidence` could name a manager that never ran ------------------------------------------


class _Auto:
    """The two methods the label depends on, isolated from the rest of the loop."""

    def __init__(self, manager: object | None, use_manager: bool) -> None:
        self.manager = manager
        self.config = type("C", (), {"use_manager": use_manager})()

    _manager_ran = None  # replaced below


def _label(manager: object | None, use_manager: bool, *, diff: bool, ok: bool) -> str:
    from chimera.core.autonomous import AutonomousAgent

    auto = _Auto(manager, use_manager)
    ran = AutonomousAgent._manager_ran(auto)  # type: ignore[arg-type]
    if diff:
        return "diff+manager" if ran else "diff"
    if ok and ran:
        return "manager"
    return "none"


def test_no_manager_is_not_labelled_manager() -> None:
    """The receipt's whole job is to say who approved. It was the one field that could be
    fabricated by omission: with no reviewer configured `_review` approves vacuously, and the label
    read that as a review."""
    assert _label(None, True, diff=False, ok=True) == "none"
    assert _label(object(), False, diff=False, ok=True) == "none"


def test_a_measured_diff_with_no_manager_is_diff_not_diff_plus_manager() -> None:
    # "diff+manager" claims two authorities. Only one of them was there.
    assert _label(None, True, diff=True, ok=True) == "diff"


def test_a_real_manager_still_gets_its_name_on_the_receipt() -> None:
    assert _label(object(), True, diff=False, ok=True) == "manager"
    assert _label(object(), True, diff=True, ok=True) == "diff+manager"


# --- 4. the only third-party entry point was the only one that skipped the validator ------------


def test_skills_import_refuses_an_invalid_card(tmp_path: Path, monkeypatch: Any) -> None:
    """The gate was applied to the code we wrote and not to the code we were handed, which is
    backwards. A skill card ends up in the system prompt, so an unvalidated one is an instruction
    from a stranger carrying the standing of an instruction from the owner."""
    from typer.testing import CliRunner

    from chimera.cli.main import app

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    card = tmp_path / "SKILL.md"
    # A name the validator rejects (not snake_case) — the cheapest rule to trip deliberately.
    card.write_text("---\nname: Not A Valid Name!\ndescription: x\n---\n\nDo the thing.\n", "utf-8")

    result = CliRunner().invoke(app, ["skills-import", str(card)])

    assert result.exit_code == 1
    assert "Refused" in result.stdout
    assert not (tmp_path / "home" / "skills.json").exists(), "it was stored anyway"
