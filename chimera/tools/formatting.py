"""Opt-in project formatting after a successful workspace write.

The formatter is selected only from project configuration and is invoked as an argv vector. This
avoids turning a formatting convenience into a shell-shaped escape hatch; the same host-execution
posture that gates ``run_shell`` is checked before the process is started.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

from chimera.sandbox.base import SandboxResult

_FORMAT_TIMEOUT = 20
_OUTPUT_LIMIT = 500


class _Executor(Protocol):
    def __call__(self, argv: list[str], *, timeout: int, cwd: Path) -> SandboxResult: ...


def _formatter(path: Path, workspace: Path) -> tuple[str, list[str]] | None:
    """Return a project-declared formatter; a file extension alone is not permission to run one."""
    suffix = path.suffix.lower()
    if suffix in {".py", ".pyi"}:
        configured = any((workspace / name).is_file() for name in ("ruff.toml", ".ruff.toml"))
        pyproject = workspace / "pyproject.toml"
        if not configured and pyproject.is_file():
            try:
                with pyproject.open("rb") as source:
                    tool_config = tomllib.load(source).get("tool", {})
                configured = isinstance(tool_config, dict) and "ruff" in tool_config
            except (OSError, tomllib.TOMLDecodeError):
                return None
        executable = shutil.which("ruff")
        if not configured or not executable:
            return None
        return "ruff format", [executable, "format", str(path)]
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".json", ".css", ".md", ".yaml", ".yml"}:
        package = workspace / "package.json"
        if not package.is_file():
            return None
        try:
            declaration = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(declaration, dict):
            return None
        dependencies = declaration.get("dependencies", {})
        dev_dependencies = declaration.get("devDependencies", {})
        if not isinstance(dependencies, dict) or not isinstance(dev_dependencies, dict):
            return None
        deps = {**dependencies, **dev_dependencies}
        executable = shutil.which("prettier")
        if "prettier" not in deps or not executable:
            return None
        return "prettier", [executable, "--write", str(path)]
    if suffix == ".go":
        executable = shutil.which("gofmt")
        if not (workspace / "go.mod").is_file() or not executable:
            return None
        return "gofmt", [executable, "-w", str(path)]
    if suffix == ".rs":
        executable = shutil.which("rustfmt")
        if not (workspace / "Cargo.toml").is_file() or not executable:
            return None
        return "rustfmt", [executable, str(path)]
    return None


def format_after_edit(
    path: Path,
    workspace: Path,
    *,
    enabled: bool,
    host_exec_allowed: bool,
    executor: _Executor | None = None,
) -> str:
    """Format one changed file without rolling back the edit if formatting fails or is gated."""
    if not enabled:
        return ""
    try:
        resolved = path.resolve()
        root = workspace.resolve()
    except (OSError, RuntimeError) as exc:
        return f"formatter error: {str(exc)[:_OUTPUT_LIMIT]}"

    if resolved == root or not resolved.is_relative_to(root):
        return "formatter skipped: file is outside the workspace"
    if not host_exec_allowed:
        return "formatter skipped: host commands need approval in this folder"
    try:
        selected = _formatter(resolved, root)
    except Exception as exc:  # noqa: BLE001 — config parsing cannot turn a landed edit into failure
        return f"formatter error: {str(exc)[:_OUTPUT_LIMIT]}"
    if selected is None:
        return "formatter skipped: no configured formatter available for this file"
    name, argv = selected
    try:
        before = _digest(resolved)
        run = executor or _run_local_argv
        result = run(argv, timeout=_FORMAT_TIMEOUT, cwd=root)
    except Exception as exc:  # noqa: BLE001 — formatting must never undo a successful edit
        return f"formatter error ({name}): {str(exc)[:_OUTPUT_LIMIT]}"
    if result.timed_out:
        return f"formatter error ({name}): timed out after {_FORMAT_TIMEOUT}s"
    if result.exit_code != 0:
        detail = result.output.strip()[:_OUTPUT_LIMIT]
        return f"formatter error ({name}): {detail or f'exit {result.exit_code}'}"
    try:
        changed = before != _digest(resolved)
    except OSError as exc:
        return f"formatter error ({name}): {str(exc)[:_OUTPUT_LIMIT]}"
    return f"formatter: {name} {'changed' if changed else 'unchanged'} the file"


def _digest(path: Path) -> bytes:
    return hashlib.sha256(path.read_bytes()).digest()


def _run_local_argv(argv: list[str], *, timeout: int, cwd: Path) -> SandboxResult:
    """Use the local sandbox's argv execution path; no shell parses formatter arguments."""
    from chimera.sandbox import get_sandbox

    sandbox = get_sandbox()
    supported = getattr(sandbox, "argv_supported", lambda: False)
    if not callable(supported) or not supported():
        return SandboxResult(exit_code=1, stderr="configured sandbox does not support argv execution")
    run_argv = getattr(sandbox, "run_argv", None)
    if not callable(run_argv):
        return SandboxResult(exit_code=1, stderr="configured sandbox does not support argv execution")
    return cast(_Executor, run_argv)(argv, timeout=timeout, cwd=cwd)


def formatter_enabled() -> bool:
    """Read the owner's opt-in switch at the point of the write."""
    from chimera.config import get_settings

    return bool(get_settings().format_after_edit)


def host_formatter_allowed() -> bool:
    """Format only under explicit host allow; ask/deny never trigger an extra prompt or bypass."""
    from chimera.config import get_settings
    from chimera.sandbox import LocalSandbox, get_sandbox
    from chimera.sandbox.confirm import sandbox_is_isolated

    settings = get_settings()
    sandbox = get_sandbox()
    supports_argv = bool(getattr(sandbox, "argv_supported", lambda: False)())
    # run_shell skips its host confirmation only when the selected backend genuinely isolates.
    # Keep that same distinction here: an explicit host allow is needed for LocalSandbox, while a
    # proven OS sandbox can run without asking or silently falling back to the host.
    host_allowed = (settings.host_exec or "ask").lower() == "allow"
    return supports_argv and (
        (host_allowed and isinstance(sandbox, LocalSandbox)) or sandbox_is_isolated(sandbox)
    )


def observe_format(path: Path, workspace: Path) -> str:
    """Small adapter shared by all writers; keep policy decisions at the formatting boundary."""
    try:
        enabled = formatter_enabled()
        if not enabled:
            return ""
        allowed = host_formatter_allowed()
    except Exception as exc:  # noqa: BLE001 — config errors must not turn a landed write into a failure
        return f"formatter error: {str(exc)[:_OUTPUT_LIMIT]}"
    return format_after_edit(path, workspace, enabled=True, host_exec_allowed=allowed)


def append_format_observation(result: str, path: Path, workspace: Path) -> str:
    note = observe_format(path, workspace)
    return f"{result}\n{note}" if note else result


# The explicit type is also useful to tests that simulate the process without invoking a formatter.
FormatterExecutor = Callable[..., SandboxResult]
