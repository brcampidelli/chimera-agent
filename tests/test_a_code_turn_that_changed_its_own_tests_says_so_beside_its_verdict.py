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


def _turn(tmp_path: Path, monkeypatch: Any, writes: dict[str, str],
          setup: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
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
    settings = Settings(CHIMERA_HOME=str(home))
    client = TestClient(
        build_api_app(lambda: ChatSession(_Writes(ws, {})), workspace=ws, settings=settings)
    )
    response = client.post("/api/code/turn", json={"message": "fix the bug"})
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
