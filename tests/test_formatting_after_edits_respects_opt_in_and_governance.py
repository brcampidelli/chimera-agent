"""The optional formatter follows file writes, but never turns them into an ungated command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.sandbox.base import SandboxResult
from chimera.sandbox.local import LocalSandbox
from chimera.sandbox.os_sandbox import OsSandbox
from chimera.tools.edit import ApplyPatchTool, EditBatchTool, EditFileTool
from chimera.tools.files import WriteFileTool
from chimera.tools.formatting import format_after_edit


def _project(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text('[tool.ruff]\nline-length = 100\n', encoding="utf-8")
    return tmp_path


def _fake_ruff(monkeypatch: Any) -> None:
    monkeypatch.setattr("chimera.tools.formatting.shutil.which", lambda name: "/bin/ruff" if name == "ruff" else None)


def test_opt_in_off_does_not_invoke_executor(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_text("x=1\n", encoding="utf-8")
    called = False

    def executor(argv: list[str], *, timeout: int, cwd: Path) -> SandboxResult:
        nonlocal called
        called = True
        return SandboxResult(0)

    result = format_after_edit(target, tmp_path, enabled=False, host_exec_allowed=True, executor=executor)
    assert result == ""
    assert not called


def test_ruff_uses_argv_absolute_file_and_workspace_cwd(tmp_path: Path, monkeypatch: Any) -> None:
    _fake_ruff(monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    target = _project(project) / "sample.py"
    target.write_text("x=1\n", encoding="utf-8")
    observed: dict[str, Any] = {}

    def executor(argv: list[str], *, timeout: int, cwd: Path) -> SandboxResult:
        observed.update(argv=argv, timeout=timeout, cwd=cwd)
        target.write_text("x = 1\n", encoding="utf-8")
        return SandboxResult(0)

    result = format_after_edit(
        target, project, enabled=True, host_exec_allowed=True, executor=executor
    )
    assert result == "formatter: ruff format changed the file"
    assert observed == {
        "argv": ["/bin/ruff", "format", str(target.resolve())],
        "timeout": 20,
        "cwd": project.resolve(),
    }


def test_missing_project_formatter_config_skips(tmp_path: Path, monkeypatch: Any) -> None:
    _fake_ruff(monkeypatch)
    target = tmp_path / "sample.py"
    target.write_text("x=1\n", encoding="utf-8")
    result = format_after_edit(
        target, tmp_path, enabled=True, host_exec_allowed=True,
        executor=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    assert result == "formatter skipped: no configured formatter available for this file"


def test_host_gate_allows_only_shell_compatible_sandbox_posture(
    monkeypatch: Any,
) -> None:
    from chimera.config import Settings
    from chimera.tools.formatting import host_formatter_allowed

    monkeypatch.setattr(
        "chimera.config.get_settings",
        lambda: Settings(_env_file=None, CHIMERA_HOST_EXEC="ask"),
    )
    monkeypatch.setattr("chimera.sandbox.confirm.sandbox_is_isolated", lambda sandbox: False)
    monkeypatch.setattr("chimera.sandbox.get_sandbox", lambda: LocalSandbox())
    assert not host_formatter_allowed()

    monkeypatch.setattr(
        "chimera.config.get_settings",
        lambda: Settings(_env_file=None, CHIMERA_HOST_EXEC="allow"),
    )
    monkeypatch.setattr("chimera.sandbox.get_sandbox", lambda: OsSandbox())
    monkeypatch.setattr("chimera.sandbox.confirm.sandbox_is_isolated", lambda sandbox: True)
    monkeypatch.setattr(OsSandbox, "argv_supported", lambda self: True)
    assert host_formatter_allowed()


def test_prettier_requires_project_declaration_and_uses_argv(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "chimera.tools.formatting.shutil.which",
        lambda name: "/bin/prettier" if name == "prettier" else None,
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"prettier": "^3"}}), encoding="utf-8"
    )
    target = tmp_path / "source.ts"
    target.write_text("const x=1\n", encoding="utf-8")
    observed: dict[str, Any] = {}

    def executor(argv: list[str], *, timeout: int, cwd: Path) -> SandboxResult:
        observed.update(argv=argv, timeout=timeout, cwd=cwd)
        return SandboxResult(0)

    result = format_after_edit(
        target, tmp_path, enabled=True, host_exec_allowed=True, executor=executor
    )
    assert result == "formatter: prettier unchanged the file"
    assert observed == {
        "argv": ["/bin/prettier", "--write", str(target.resolve())],
        "timeout": 20,
        "cwd": tmp_path.resolve(),
    }


def test_go_and_rust_formatters_require_project_files(tmp_path: Path, monkeypatch: Any) -> None:
    executables = {"gofmt": "/bin/gofmt", "rustfmt": "/bin/rustfmt"}
    monkeypatch.setattr("chimera.tools.formatting.shutil.which", executables.get)
    go = tmp_path / "main.go"
    rust = tmp_path / "main.rs"
    go.write_text("package main\n", encoding="utf-8")
    rust.write_text("fn main() {}\n", encoding="utf-8")
    def never_run(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("must not run")

    assert format_after_edit(go, tmp_path, enabled=True, host_exec_allowed=True, executor=never_run) == (
        "formatter skipped: no configured formatter available for this file"
    )
    assert format_after_edit(rust, tmp_path, enabled=True, host_exec_allowed=True, executor=never_run) == (
        "formatter skipped: no configured formatter available for this file"
    )

    (tmp_path / "go.mod").write_text("module example.test/project\n", encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "example"\n', encoding="utf-8")
    calls: list[list[str]] = []

    def executor(argv: list[str], *, timeout: int, cwd: Path) -> SandboxResult:
        calls.append(argv)
        return SandboxResult(0)

    assert format_after_edit(go, tmp_path, enabled=True, host_exec_allowed=True, executor=executor) == (
        "formatter: gofmt unchanged the file"
    )
    assert format_after_edit(rust, tmp_path, enabled=True, host_exec_allowed=True, executor=executor) == (
        "formatter: rustfmt unchanged the file"
    )
    assert calls == [["/bin/gofmt", "-w", str(go.resolve())], ["/bin/rustfmt", str(rust.resolve())]]


def test_host_approval_requirement_skips_formatter(tmp_path: Path, monkeypatch: Any) -> None:
    _fake_ruff(monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    target = _project(project) / "sample.py"
    target.write_text("x=1\n", encoding="utf-8")
    result = format_after_edit(
        target, project, enabled=True, host_exec_allowed=False,
        executor=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    assert result == "formatter skipped: host commands need approval in this folder"


def test_formatter_failure_keeps_the_completed_edit_and_reports_error(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _fake_ruff(monkeypatch)
    _project(tmp_path)
    monkeypatch.setenv("CHIMERA_FORMAT_AFTER_EDIT", "true")
    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    from chimera.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "chimera.tools.formatting._run_local_argv",
        lambda *args, **kwargs: SandboxResult(2, stderr="formatter broke"),
    )
    try:
        result = WriteFileTool(tmp_path).run(path="sample.py", content="x = 2\n")
        assert result == (
            "wrote 6 chars to sample.py\nformatter error (ruff format): formatter broke"
        )
        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == "x = 2\n"
    finally:
        get_settings.cache_clear()


def test_file_outside_workspace_is_never_formatted(tmp_path: Path, monkeypatch: Any) -> None:
    _fake_ruff(monkeypatch)
    workspace = _project(tmp_path / "workspace")
    target = tmp_path / "outside.py"
    target.write_text("x=1\n", encoding="utf-8")
    result = format_after_edit(
        target, workspace, enabled=True, host_exec_allowed=True,
        executor=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    assert result == "formatter skipped: file is outside the workspace"


def test_all_edit_tools_attach_formatter_result(monkeypatch: Any, tmp_path: Path) -> None:
    _fake_ruff(monkeypatch)
    _project(tmp_path)
    monkeypatch.setenv("CHIMERA_FORMAT_AFTER_EDIT", "true")
    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    from chimera.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr("chimera.tools.formatting._run_local_argv", lambda *args, **kwargs: SandboxResult(0))
    target = tmp_path / "sample.py"
    target.write_text("x = 1\n", encoding="utf-8")
    assert "formatter: ruff format unchanged the file" in EditFileTool(tmp_path).run(
        path="sample.py", old="x = 1", new="x = 2"
    )
    assert "formatter: ruff format unchanged the file" in ApplyPatchTool(tmp_path).run(
        path="sample.py", patch="<<<<<<< SEARCH\nx = 2\n=======\nx = 3\n>>>>>>> REPLACE"
    )
    assert "formatter: ruff format unchanged the file" in EditBatchTool(tmp_path).run(
        edits=[{"path": "sample.py", "old": "x = 3", "new": "x = 4"}]
    )
    assert "formatter: ruff format unchanged the file" in WriteFileTool(tmp_path).run(
        path="sample.py", content="x = 5\n"
    )
    get_settings.cache_clear()


def test_write_remains_successful_when_formatter_raises(monkeypatch: Any, tmp_path: Path) -> None:
    _fake_ruff(monkeypatch)
    _project(tmp_path)
    monkeypatch.setenv("CHIMERA_FORMAT_AFTER_EDIT", "true")
    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    from chimera.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "chimera.tools.formatting._run_local_argv",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("cannot execute formatter")),
    )
    try:
        result = WriteFileTool(tmp_path).run(path="sample.py", content="x = 5\n")
        assert "formatter error (ruff format): cannot execute formatter" in result
        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == "x = 5\n"
    finally:
        get_settings.cache_clear()
