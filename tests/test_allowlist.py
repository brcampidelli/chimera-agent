"""Tests for the per-session tool allowlist (issue #4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.governance import AuditLog, restrict_registry
from chimera.tools import default_registry
from chimera.tools.builtin import EchoTool
from chimera.tools.registry import ToolRegistry


def _registry(*names: str) -> ToolRegistry:
    reg = ToolRegistry()
    for name in names:
        tool = EchoTool()
        tool.name = name
        reg.register(tool)
    return reg


def test_allow_none_keeps_everything() -> None:
    reg = _registry("read_file", "run_shell", "http_get")
    kept = restrict_registry(reg, allow=None)
    assert set(kept.names()) == {"read_file", "run_shell", "http_get"}


def test_allow_grants_only_listed() -> None:
    reg = _registry("read_file", "run_shell", "http_get")
    kept = restrict_registry(reg, allow=["read_file", "http_get"])
    assert set(kept.names()) == {"read_file", "http_get"}
    assert "run_shell" not in kept  # the un-granted tool is dropped, not just gated


def test_empty_allowlist_grants_nothing() -> None:
    # An explicit empty allowlist is a fully locked session (distinct from allow=None).
    reg = _registry("read_file", "run_shell")
    kept = restrict_registry(reg, allow=[])
    assert kept.names() == []


def test_deny_wins_over_allow() -> None:
    reg = _registry("read_file", "run_shell")
    kept = restrict_registry(reg, allow=["read_file", "run_shell"], deny=["run_shell"])
    assert set(kept.names()) == {"read_file"}


def test_deny_only_removes_named() -> None:
    reg = _registry("read_file", "run_shell", "http_get")
    kept = restrict_registry(reg, deny=["run_shell"])
    assert set(kept.names()) == {"read_file", "http_get"}


def test_unknown_names_are_ignored() -> None:
    reg = _registry("read_file")
    kept = restrict_registry(reg, allow=["read_file", "does_not_exist"])
    assert kept.names() == ["read_file"]


def test_whitespace_names_are_trimmed() -> None:
    reg = _registry("read_file", "run_shell")
    kept = restrict_registry(reg, allow=[" read_file ", "  "])
    assert kept.names() == ["read_file"]


def test_returns_a_new_registry_leaving_the_original_intact() -> None:
    reg = _registry("read_file", "run_shell")
    kept = restrict_registry(reg, allow=["read_file"])
    assert kept is not reg
    assert set(reg.names()) == {"read_file", "run_shell"}  # source untouched


def test_audit_records_exclusions(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    reg = _registry("read_file", "run_shell", "http_get")
    restrict_registry(reg, allow=["read_file"], audit=audit)
    entries = audit.entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["type"] == "tool_allowlist"
    assert entry["allow"] == ["read_file"]
    assert set(entry["excluded"]) == {"run_shell", "http_get"}
    assert entry["kept"] == ["read_file"]


def test_no_audit_entry_when_nothing_excluded(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    reg = _registry("read_file", "run_shell")
    restrict_registry(reg, allow=["read_file", "run_shell"], audit=audit)
    assert audit.entries() == []  # nothing dropped → nothing to record


def test_default_registry_can_be_locked_to_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # Realistic use: grant a read-only session (no shell, no code exec, no http).
    from chimera.config import get_settings

    get_settings.cache_clear()
    reg = default_registry()
    read_only = restrict_registry(reg, allow=["read_file", "list_dir", "grep", "glob"])
    names = set(read_only.names())
    assert names == {"read_file", "list_dir", "grep", "glob"}
    assert "run_shell" not in names and "execute_code" not in names
