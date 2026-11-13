"""The Hooks screen says, per hook, whether it runs on this machine and how often it was skipped.

On Windows a repository's hook has no sandbox that isolates, so it is loaded and skipped, and follows
`onFailure` (continue by default): a "deny" guard the owner thinks is on is inert there, and before
this the list showed it like any other (audit 2026-11, a07-12). `onFailure` is unchanged here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.governance import claude_hooks as ch
from tests.test_claude_format_hooks import _entry, _rig, _Sandbox, _settings_obj


def test_a_hook_with_no_isolating_sandbox_is_skipped_counted_and_marked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ch, "_SKIPPED", {})
    tool, runner, inner, box = _rig(tmp_path, _entry("PreToolUse"), sandbox=_Sandbox(isolated=False))
    for _ in range(3):
        assert tool.run(command="ls") == "ran", "onFailure continue: the call still runs"
    assert box.commands == []
    hook = runner.config.hooks[0]
    assert ch.skipped_count(hook) == 3

    ws, data = tmp_path / "project", tmp_path / "data"
    ch.trust_repo(data, ws)
    out = ch.describe(_settings_obj(home=data, sandbox="local"), ws)
    shown = out["hooks"][0]
    assert shown["runs_here"] is False
    assert shown["skipped"] == 3


def test_a_hook_that_runs_is_not_counted_as_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ch, "_SKIPPED", {})
    tool, runner, _inner, box = _rig(tmp_path, _entry("PreToolUse"), sandbox=_Sandbox(isolated=True))
    assert tool.run(command="ls") == "ran"
    assert len(box.commands) == 1
    assert ch.skipped_count(runner.config.hooks[0]) == 0


def test_runs_here_is_unknown_when_it_would_need_docker(tmp_path: Path) -> None:
    from tests.test_claude_format_hooks import _settings

    _settings(tmp_path, _entry("PreToolUse"))
    ws, data = tmp_path / "project", tmp_path / "data"
    ch.trust_repo(data, ws)
    out = ch.describe(_settings_obj(home=data, sandbox="docker"), ws)
    assert out["hooks"][0]["runs_here"] is None
