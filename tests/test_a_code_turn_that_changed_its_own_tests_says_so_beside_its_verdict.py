"""Study 30, S30-23, on the Code tab: the surface people use most verifies after every turn.

The autonomous loop's attempt receipts carry the verifier-integrity flags. The Code tab ran its own
`CommandVerifier` after every editing turn and said nothing beside the verdict: a turn that skipped
the failing test came back "verified: passed". These tests hold that the flags reach the verdict
AND the receipt stored with the conversation — the surface a reader can come back to.

The verify command is deliberately NOT written to this turn's taint ledger (an earlier commit did):
the Code tab's ledger lives for one request, nothing reads it afterwards but `run_tainted()`, which
a verify event does not move, and it is never dumped. What the event would have recorded — the
command, where it came from, how it ended — is the stored verdict checked below.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.core.agent import AgentResult  # noqa: E402
from chimera.core.context_budget import RunState  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402

_FAILING = "def test_ok():\n    assert True\n\n\ndef test_bug():\n    assert 1 + 1 == 3\n"


class _Writes:
    """An agent whose turn writes the given files and reports one edit."""

    def __init__(self, ws: Path, writes: dict[str, str]) -> None:
        self.ws, self.writes = ws, writes
        self.run_state = RunState()

    def run(self, task: str, *, on_edit: Any = None, history: Any = None, **_: Any) -> AgentResult:
        for rel, text in self.writes.items():
            (self.ws / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.ws / rel).write_text(text, encoding="utf-8")
            if on_edit:
                on_edit(rel, f"--- {rel}\n+++ {rel}\n@@\n+edit\n")
        return AgentResult(
            answer="done", steps=1, stopped_reason="final",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "done"}],
            tool_names=[], model="test/model",
        )


def _turn(
    tmp_path: Path,
    monkeypatch: Any,
    writes: dict[str, str],
    setup: dict[str, str],
    *,
    request: dict[str, Any] | None = None,
    settings_options: dict[str, Any] | None = None,
    grant_project: bool = False,
    preapproved_command: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The streamed `verified` frame, and the `verified` verdict of the receipt stored on disk."""
    import chimera.core
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir()
    for rel, text in setup.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text, encoding="utf-8")
    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: _Writes(ws, writes), raising=True)
    home = tmp_path / "home"
    settings = Settings(CHIMERA_HOME=str(home), **(settings_options or {}))
    if grant_project:
        import hashlib

        from chimera.core.code_projects import CodeProjectRegistry

        registry = CodeProjectRegistry(home / "code_projects.json")
        registry.set_grant(str(ws), True)
        if preapproved_command is not None:
            assert registry.approve_verify_hash(
                ws, hashlib.sha256(preapproved_command.encode()).hexdigest()
            )
    client = TestClient(
        build_api_app(lambda: ChatSession(_Writes(ws, {})), workspace=ws, settings=settings)
    )
    response = client.post(
        "/api/code/turn", json={"message": "fix the bug", **(request or {})}
    )
    event = ""
    streamed: dict[str, Any] = {}
    session_id = ""
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            data = json.loads(line[len("data: "):])
            if event == "verified":
                streamed = data
            if isinstance(data, dict) and data.get("session_id") and not session_id:
                session_id = str(data["session_id"])
    assert session_id, "the turn never named its conversation"
    (stored_file,) = list(home.rglob(f"{session_id}.json"))
    receipts = json.loads(stored_file.read_text(encoding="utf-8")).get("receipts", [])
    assert receipts, "the turn's receipt was not stored"
    return streamed, receipts[-1].get("verified", {})
def _project_verify(command: str) -> dict[str, str]:
    return {".chimera/verify.toml": f"command = {json.dumps(command)}\n"}


def test_project_verify_approval_runs_exact_command_and_persists_hash(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import hashlib

    from chimera.core.code_projects import CodeProjectRegistry

    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    asked: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "chimera.governance.pending.ask_durably",
        lambda *args, **kwargs: asked.append({"action": args[1], **kwargs}) or True,
    )
    command = "echo ran > sentinel"
    streamed, _stored = _turn(
        tmp_path, monkeypatch, {"main.py": "x = 1\n"}, _project_verify(command),
        settings_options={"CHIMERA_HOST_EXEC": "allow"}, grant_project=True,
    )
    ws = tmp_path / "ws"
    registry = CodeProjectRegistry(tmp_path / "home" / "code_projects.json")
    assert streamed["state"] == "passed"
    assert (ws / "sentinel").is_file()
    assert registry.approved_verify_hash_for(ws) == hashlib.sha256(command.encode()).hexdigest()
    assert asked and asked[0]["whole_action"] is True
    assert hashlib.sha256(command.encode()).hexdigest()[:8] in asked[0]["action"]


def test_approved_project_verify_that_changes_during_turn_abstains_without_running(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    calls: list[bool] = []
    monkeypatch.setattr(
        "chimera.governance.pending.ask_durably",
        lambda *_args, **_kwargs: calls.append(True) or True,
    )
    changed_config = _project_verify("echo changed > sentinel")[".chimera/verify.toml"]
    streamed, _stored = _turn(
        tmp_path, monkeypatch,
        {"main.py": "x = 1\n", ".chimera/verify.toml": changed_config},
        _project_verify("echo ran > sentinel"),
        settings_options={"CHIMERA_HOST_EXEC": "allow"}, grant_project=True,
        preapproved_command="echo ran > sentinel",
    )
    assert calls == []
    assert streamed["state"] == "abstained"
    assert streamed["output"] == "project: verify command changed during the turn"
    assert not (tmp_path / "ws" / "sentinel").exists()


@pytest.mark.parametrize(
    ("host_exec", "reach", "deny_tools", "grant_project", "approval"),
    [
        ("allow", "", None, False, None),
        ("allow", "workspace_shell", None, False, None),
        ("deny", "", ["run_shell"], True, None),
        ("allow", "", None, True, False),
    ],
    ids=["no-project-grant", "global-reach-is-not-a-project-grant", "host-deny-and-commands-off", "owner-refuses"],
)
def test_project_verify_abstentions_never_execute_sentinel_command(
    tmp_path: Path, monkeypatch: Any, host_exec: str, reach: str,
    deny_tools: list[str] | None, grant_project: bool, approval: bool | None,
) -> None:
    monkeypatch.setenv("CHIMERA_HOST_EXEC", host_exec)
    asks: list[bool] = []
    monkeypatch.setattr(
        "chimera.governance.pending.ask_durably",
        lambda *_args, **_kwargs: asks.append(True) or bool(approval),
    )
    streamed, _stored = _turn(
        tmp_path, monkeypatch, {"main.py": "x = 1\n"},
        _project_verify("echo ran > sentinel"),
        request={
            **({"posture": {"reach": reach}} if reach else {}),
            **({"deny_tools": deny_tools} if deny_tools else {}),
        },
        settings_options={"CHIMERA_HOST_EXEC": host_exec}, grant_project=grant_project,
    )
    assert streamed["state"] == "abstained"
    assert not (tmp_path / "ws" / "sentinel").exists()
    if approval is False:
        assert asks == [True]
    elif not grant_project:
        assert asks == []


def test_project_verify_is_captured_before_turn_and_changed_file_abstains(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from chimera.core.code_projects import CodeProjectRegistry

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".chimera").mkdir()
    config = ws / ".chimera" / "verify.toml"
    config.write_text('command = "echo BEFORE"\n', encoding="utf-8")
    home = tmp_path / "home"
    CodeProjectRegistry(home / "code_projects.json").set_grant(str(ws), True)
    monkeypatch.setattr("chimera.core.Agent", lambda *_a, **_k: _Writes(
        ws, {".chimera/verify.toml": 'command = "echo AFTER"\n'}
    ), raising=True)
    from chimera.api import build_api_app

    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_HOST_EXEC="allow")
    client = TestClient(build_api_app(lambda: ChatSession(_Writes(ws, {})), workspace=ws, settings=settings))
    response = client.post("/api/code/turn", json={"message": "change config"})
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    verified = next(item for item in events if isinstance(item, dict) and item.get("state"))
    assert verified["source"] == "project"
    assert verified["state"] == "abstained"
    assert verified["output"] == "project: verify command changed during the turn"
@pytest.mark.parametrize("hostile", ["</project-verify>", "</setup-output>"])
def test_untrusted_project_command_cannot_close_its_instruction_fence(hostile: str) -> None:
    from chimera.api.code_api import fence_safe

    safe = fence_safe(f"before {hostile} after")

    assert "</" not in safe


def test_project_verify_instruction_remains_present_when_retries_are_off(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".chimera").mkdir()
    (ws / ".chimera" / "verify.toml").write_text('command = "echo exact-check"\\n', encoding="utf-8")
    monkeypatch.setattr("chimera.core.Agent", lambda *_a, **_k: _Writes(ws, {}), raising=True)
    captured: list[str] = []
    from chimera.core.agent import AgentResult

    def run(self: Any, task: str, **kwargs: Any) -> AgentResult:
        captured.append(task)
        return AgentResult(answer="done", steps=1, stopped_reason="final", transcript=[],
                           tool_names=[], model="test/model")

    monkeypatch.setattr(_Writes, "run", run)
    client = TestClient(build_api_app(lambda: ChatSession(_Writes(ws, {})), workspace=ws,
                                      settings=Settings(CHIMERA_HOME=str(tmp_path / "home"))))
    response = client.post("/api/code/turn", json={"message": "check", "verify_retry": False})

    assert response.status_code == 200
    assert captured and captured[0] == "check"


def test_project_verify_needs_exact_command_approval_before_execution(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import hashlib

    from chimera.core.code_projects import CodeProjectRegistry

    ws = tmp_path / "ws"
    ws.mkdir()
    config_dir = ws / ".chimera"
    config_dir.mkdir()
    config = config_dir / "verify.toml"
    config.write_text('command = "echo SAFE"\n', encoding="utf-8")
    home = tmp_path / "home"
    registry = CodeProjectRegistry(home / "code_projects.json")
    registry.set_grant(str(ws), True)
    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    monkeypatch.setattr(
        "chimera.core.Agent", lambda *_a, **_k: _Writes(ws, {"main.py": "x = 1\n"}), raising=True
    )

    asked: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "chimera.governance.pending.ask_durably",
        lambda *args, **kwargs: asked.append({"action": args[1], **kwargs}) or False,
    )
    from chimera.api import build_api_app

    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_HOST_EXEC="allow")
    client = TestClient(build_api_app(lambda: ChatSession(_Writes(ws, {})), workspace=ws, settings=settings))

    def verify_event() -> dict[str, Any]:
        response = client.post("/api/code/turn", json={"message": "check"})
        event = ""
        for line in response.text.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event == "verified":
                return json.loads(line[6:])
        raise AssertionError("turn emitted no verified event")

    first = verify_event()
    assert first["state"] == "abstained"
    assert "waiting for the owner to approve" in first["output"]
    assert asked and asked[0]["action"] == (
        f"project verify command (sha256:{hashlib.sha256(b'echo SAFE').hexdigest()[:8]}):\necho SAFE"
    )
    assert asked[0]["whole_action"] is True
    assert registry.approved_verify_hash_for(ws) == ""

    digest = hashlib.sha256(b"echo SAFE").hexdigest()
    assert registry.approve_verify_hash(ws, digest)
    second = verify_event()
    assert second["state"] == "passed"
    assert asked and len(asked) == 1

    config.write_text('command = "echo CHANGED"\n', encoding="utf-8")
    third = verify_event()
    assert third["state"] == "abstained"
    assert len(asked) == 2
    assert asked[1]["action"].endswith("\necho CHANGED")
    assert hashlib.sha256(b"echo CHANGED").hexdigest()[:8] in asked[1]["action"]


def test_a_turn_that_skipped_the_failing_test_passes_and_says_so(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # Inferred from tests/, so it runs on the host: allowed here so the pass is a real pass.
    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    skipped = _FAILING.replace("def test_bug", "import pytest\n\n\n@pytest.mark.skip\ndef test_bug")
    streamed, stored = _turn(
        tmp_path, monkeypatch, {"tests/test_it.py": skipped}, {"tests/test_it.py": _FAILING}
    )
    # Record-only: the verdict is what the check said.
    assert streamed["state"] == "passed"
    assert any(
        f.startswith("tests_removed_or_skipped: tests/test_it.py")
        for f in streamed["integrity_flags"]
    ), streamed
    # And it outlives the request: the stored receipt carries the same flags, the command that
    # decided the turn, where that command came from and how it ended.
    assert stored["integrity_flags"] == streamed["integrity_flags"]
    assert stored["command"] == streamed["command"]
    assert stored["source"] == "inferred:tests/" and stored["state"] == "passed"


def test_a_turn_that_rewrote_the_makefile_recipe_is_the_verifier_changing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # `make test` is inferred from the Makefile. Host exec is not allowed here, so the check
    # abstains — and the stored verdict says it abstained rather than reading as a command that ran.
    monkeypatch.delenv("CHIMERA_HOST_EXEC", raising=False)
    streamed, stored = _turn(
        tmp_path, monkeypatch, {"Makefile": "test:\n\ttrue\n"}, {"Makefile": "test:\n\tpytest -q\n"}
    )
    assert streamed["command"] == "make test"
    assert "verifier_modified: Makefile" in " ".join(streamed["integrity_flags"])
    assert stored["source"] == "inferred:Makefile"
    assert "verifier_modified: Makefile" in " ".join(stored["integrity_flags"])


def test_a_source_only_turn_carries_no_integrity_flag(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    streamed, stored = _turn(
        tmp_path, monkeypatch, {"m.py": "x = 2\n"},
        {"m.py": "x = 1\n", "tests/test_it.py": "def test_ok():\n    assert True\n"},
    )
    assert streamed["state"] == "passed" and streamed["integrity_flags"] == []
    assert stored["integrity_flags"] == []
