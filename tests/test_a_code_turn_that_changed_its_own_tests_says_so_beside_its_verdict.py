"""Study 30, S30-23, on the Code tab: the surface people use most verifies after every turn.

The autonomous loop's attempt receipts carry the verifier-integrity flags and put the verify
command in the taint ledger. The Code tab ran its own `CommandVerifier` after every editing turn,
with the turn's ledger in scope, and did neither: a turn that skipped the failing test came back
"verified: passed" with nothing beside it, and the ledger replay of the turn omitted the one
command that decided it. These tests hold the same two facts for a turn.
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
from chimera.governance.ledger import CapabilityEvent, TaintLedger  # noqa: E402
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
          setup: dict[str, str]) -> tuple[dict[str, Any], list[CapabilityEvent]]:
    import chimera.core
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir()
    for rel, text in setup.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text, encoding="utf-8")
    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: _Writes(ws, writes), raising=True)
    settled: list[CapabilityEvent] = []
    original = TaintLedger.settle_verify

    def spy(self: TaintLedger, event: CapabilityEvent, outcome: str) -> None:
        original(self, event, outcome)
        settled.append(event)

    monkeypatch.setattr(TaintLedger, "settle_verify", spy)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(
        build_api_app(lambda: ChatSession(_Writes(ws, {})), workspace=ws, settings=settings)
    )
    response = client.post("/api/code/turn", json={"message": "fix the bug"})
    event = ""
    verdict: dict[str, Any] = {}
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: ") and event == "verified":
            verdict = json.loads(line[len("data: "):])
    return verdict, settled


def test_a_turn_that_skipped_the_failing_test_passes_and_says_so(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # Inferred from tests/, so it runs on the host: allowed here so the pass is a real pass.
    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    skipped = _FAILING.replace("def test_bug", "import pytest\n\n\n@pytest.mark.skip\ndef test_bug")
    verdict, settled = _turn(
        tmp_path, monkeypatch, {"tests/test_it.py": skipped}, {"tests/test_it.py": _FAILING}
    )
    # Record-only: the verdict is what the check said.
    assert verdict["state"] == "passed"
    flags = verdict["integrity_flags"]
    assert any(f.startswith("tests_removed_or_skipped: tests/test_it.py") for f in flags), flags
    # And the command that decided the turn is on the ledger, with its origin and its outcome.
    (event,) = settled
    assert event.kind == "verify" and event.ref == verdict["command"]
    assert event.detail == "source=inferred origin=tests/ outcome=passed"


def test_a_turn_that_rewrote_the_makefile_recipe_is_the_verifier_changing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # `make test` is inferred from the Makefile. Host exec is not allowed here, so the check
    # abstains — and the ledger says it abstained rather than reading as a command that ran.
    monkeypatch.delenv("CHIMERA_HOST_EXEC", raising=False)
    verdict, settled = _turn(
        tmp_path, monkeypatch, {"Makefile": "test:\n\ttrue\n"}, {"Makefile": "test:\n\tpytest -q\n"}
    )
    assert verdict["command"] == "make test"
    assert "verifier_modified: Makefile" in " ".join(verdict["integrity_flags"])
    (event,) = settled
    assert event.detail.startswith("source=inferred origin=Makefile outcome=")


def test_a_source_only_turn_carries_no_integrity_flag(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    verdict, _ = _turn(
        tmp_path, monkeypatch, {"m.py": "x = 2\n"},
        {"m.py": "x = 1\n", "tests/test_it.py": "def test_ok():\n    assert True\n"},
    )
    assert verdict["state"] == "passed" and verdict["integrity_flags"] == []
