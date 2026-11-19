"""The shared guest turn omits commands and every hook origin, with its owner as a control."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_a_guest_turn_runs_with_the_least_privilege import (
    _Agent,
    _client,
    _guest_turn,
    _owner_turn,
)

# Re-export the existing home/settings fixture once for this module's test and its teardown.
from tests.test_a_guest_turn_runs_with_the_least_privilege import _fresh_settings as _fresh_settings


def test_the_shared_guest_turn_excludes_commands_and_every_hook_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    from chimera.core.code_projects import CodeProjectRegistry
    from chimera.core.verify import CommandVerifier, VerificationResult
    from chimera.governance.claude_hooks import trust_repo
    from chimera.governance.hooks import HOOKS_FILE_NAME, HookedTool

    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    monkeypatch.setenv("CHIMERA_HOOKS", "true")
    monkeypatch.setenv("CHIMERA_CLAUDE_HOOKS_USER", "true")
    monkeypatch.setenv("CHIMERA_CLAUDE_HOOKS_REPO", "true")
    client = _client(tmp_path, monkeypatch)
    home, ws = tmp_path / "home", tmp_path / "ws"

    (home / HOOKS_FILE_NAME).write_text(
        json.dumps({"hooks": [{
            "id": "construction-note", "event": "pre_tool", "decision": "annotate", "note": "fixture",
        }]}), encoding="utf-8",
    )
    for origin, directory in (("user", tmp_path / "claude-config"), ("repo", ws / ".claude")):
        directory.mkdir(parents=True, exist_ok=True)
        # Equal commands are deduplicated across origins, so give each fixture its own label.
        claude_hook = {"hooks": {"UserPromptSubmit": [{"hooks": [
            {"type": "command", "command": f"echo {origin}-hook-fixture"},
        ]}]}}
        (directory / "settings.json").write_text(json.dumps(claude_hook), encoding="utf-8")
    trust_repo(home, ws)

    (ws / ".chimera").mkdir()
    verify_command, setup_command = "echo verify-fixture", "echo setup-fixture"
    (ws / ".chimera" / "verify.toml").write_text(
        f"command = {json.dumps(verify_command)}\nsetup = {json.dumps(setup_command)}\n",
        encoding="utf-8",
    )
    projects = CodeProjectRegistry(home / "code_projects.json")
    projects.set_grant(str(ws), True)
    assert projects.approve_verify_hash(ws, hashlib.sha256(verify_command.encode()).hexdigest())
    assert projects.approve_setup_hash(ws, hashlib.sha256(setup_command.encode()).hexdigest())
    # Keep setup eligible for both turns so the owner's successful preparation cannot mask a regression.
    monkeypatch.setattr(CodeProjectRegistry, "setup_was_prepared", lambda *args: False)
    verified_commands: list[str] = []

    def record_verify(verifier: CommandVerifier) -> VerificationResult:
        verified_commands.append(verifier.command)
        return VerificationResult(passed=True, output="offline fixture")

    monkeypatch.setattr(CommandVerifier, "verify", record_verify)
    _Agent.touch = ws / "edited.txt"

    def hook_origins(built: dict[str, Any]) -> set[str]:
        tool = built["registry"].get("read_file")
        origins: set[str] = set()
        while tool is not None:
            if isinstance(tool, HookedTool):
                origins.update(hook.origin for hook in tool.runner.config.hooks)
            tool = getattr(tool, "inner", None)
        return origins

    sid = _owner_turn(client, tmp_path)
    owner = _Agent.built[-1]
    assert owner["config"].guest is False
    assert {"run_shell", "execute_code"} <= {tool.name for tool in owner["registry"].tools()}
    assert hook_origins(owner) == {"owner", "user", "repo"}
    assert getattr(owner["registry"], "lifecycle", None) is not None
    assert verified_commands == [setup_command, verify_command]

    guest = _guest_turn(client, sid, "Summarize the project notes.")
    assert guest["config"].guest is True
    assert {"run_shell", "execute_code"}.isdisjoint(tool.name for tool in guest["registry"].tools())
    assert hook_origins(guest) == set()
    assert getattr(guest["registry"], "lifecycle", None) is None
    assert verified_commands == [setup_command, verify_command]
