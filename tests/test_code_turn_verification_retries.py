"""The Code turn can optionally feed failed verification back to its own agent loop."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.core.agent import AgentResult  # noqa: E402
from chimera.core.context_budget import RunState  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402


class _RepairAgent:
    """A tiny loop double that records repair prompts and writes each run's version."""

    runs: list[tuple[str, list[Any]]] = []
    contents: list[str] = []
    answers: list[str] = []
    retry_test_action: str | None = None
    conflict: bool = False
    retry_write_test: bool = False
    empty_conflict: bool = False
    retry_ask_count: int = 0
    retry_ask_allowed: list[bool] = []

    def __init__(self, ws: Path, _: dict[str, str]) -> None:
        self.ws = ws
        self.run_state = RunState()

    def run(self, task: str, *, on_edit: Any = None, history: Any = None, **_: Any) -> AgentResult:
        prior = list(history or [])
        type(self).runs.append((task, prior))
        version = type(self).contents[min(len(type(self).runs) - 1, len(type(self).contents) - 1)]
        (self.ws / "src.py").write_text(version, encoding="utf-8")
        if len(type(self).runs) == 1 and type(self).initial_write_test:
            from chimera.tools.files import WriteFileTool

            result = WriteFileTool(self.ws).run(
                path="tests/test_it.py", content="def test_new():\n    assert True\n",
            )
            assert result.startswith("wrote")
        if len(type(self).runs) > 1 and type(self).retry_ask_count:
            from chimera.tools.ask_user import AskUserTool
            from chimera.tools.files import WriteFileTool

            write = WriteFileTool(self.ws).run(
                path="tests/test_it.py", content="def test_existing(): pass\\n",
            )
            assert write.startswith("error:")
            result = AskUserTool(self.ws, lambda *_args, **_kwargs: None, wait_seconds=0.01).run(
                question="Allow the test edit?", options=["authorize test edits"],
            )
            type(self).retry_ask_allowed.append(not result.startswith("error:"))
        if len(type(self).runs) > 1 and type(self).retry_write_test:
            from chimera.tools.write_region import repair_writes_read_only

            with repair_writes_read_only("pytest -q"):
                test_file = self.ws / "tests" / "test_it.py"
                if type(self).retry_write_action == "write_file":
                    from chimera.tools.files import WriteFileTool

                    result = WriteFileTool(self.ws).run(
                        path="tests/test_it.py",
                        content="def test_existing():\n    assert changed_implementation()\n",
                    )
                else:
                    from chimera.tools.edit import EditFileTool

                    result = EditFileTool(self.ws).run(
                        path="tests/test_it.py", old="original_implementation",
                        new="changed_implementation",
                    )
                type(self).answers = ["refused test write", "repaired"]
                assert result.startswith("error:")
        if len(type(self).runs) > 1 and type(self).retry_test_action:
            test_file = self.ws / "tests" / "test_it.py"
            if type(self).retry_test_action == "delete":
                test_file.unlink()
            else:
                test_file.write_text(
                    "def test_existing():\n    assert changed_implementation()\n",
                    encoding="utf-8",
                )
        if on_edit:
            on_edit("src.py", "--- src.py\n+++ src.py\n@@\n-edit\n+edit\n")
        answer = (
            "CONFLICT:" if len(type(self).runs) > 1 and type(self).empty_conflict else
            "CONFLICT: the requested behavior contradicts the existing test."
            if len(type(self).runs) > 1 and type(self).conflict else
            type(self).answers[min(len(type(self).runs) - 1, len(type(self).answers) - 1)]
            if type(self).answers else
            "repaired" if len(type(self).runs) > 1 else "implemented"
        )
        transcript = [*prior, {"role": "user", "content": task},
                      {"role": "assistant", "content": answer}]
        return AgentResult(
            answer=answer, steps=1, stopped_reason="final", transcript=transcript,
            tool_names=[], model="test/model", prompt_tokens=10, completion_tokens=2, usd=0.01,
        )


def _run_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checks: list[Any], *, enabled: bool,
    answers: list[str] | None = None,
    retry_test_action: str | None = None,
    conflict: bool = False,
    retry_write_test: bool = False,
    retry_write_action: str = "write_file",
    initial_write_test: bool = False,
    empty_conflict: bool = False,
    retry_ask_count: int = 0,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    import chimera.core
    import chimera.core.verify
    from chimera.api import build_api_app

    _RepairAgent.runs = []
    _RepairAgent.contents = ["x = 1\n", "x = 2\n", "x = 3\n"]
    _RepairAgent.answers = answers or []
    _RepairAgent.retry_test_action = retry_test_action
    _RepairAgent.conflict = conflict
    _RepairAgent.retry_write_test = retry_write_test
    _RepairAgent.retry_write_action = retry_write_action
    _RepairAgent.initial_write_test = initial_write_test
    _RepairAgent.empty_conflict = empty_conflict
    _RepairAgent.retry_ask_count = retry_ask_count
    _RepairAgent.retry_ask_allowed = []
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "src.py").write_text("x = 0\n", encoding="utf-8")
    (ws / "tests").mkdir()
    (ws / "tests" / "test_it.py").write_text(
        "def test_existing():\n    assert original_implementation()\n", encoding="utf-8",
    )
    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: _RepairAgent(ws, {}))
    monkeypatch.setattr("chimera.api.app.resolve_verify", lambda _request, _ws: ("pytest -q", "user"))

    class _Verifier:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def verify(self) -> Any:
            result = checks.pop(0)
            return SimpleNamespace(
                passed=result[0], abstained=result[1], output=result[2]
            )

    monkeypatch.setattr(chimera.core.verify, "CommandVerifier", _Verifier)
    home = tmp_path / "home"
    client = TestClient(build_api_app(
        lambda: ChatSession(_RepairAgent(ws, {})), workspace=ws,
        settings=Settings(CHIMERA_HOME=str(home)),
    ))
    response = client.post("/api/code/turn", json={
        "message": "implement the change", "verify_retry": enabled,
    })
    events: dict[str, Any] = {}
    event = ""
    session_id = ""
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            data = json.loads(line[len("data: "):])
            if isinstance(data, dict):
                events[event] = data
                if data.get("session_id"):
                    session_id = str(data["session_id"])
    assert session_id, response.text
    stored_path, = home.rglob(f"{session_id}.json")
    stored = json.loads(stored_path.read_text(encoding="utf-8"))
    return events["verified"], events["done"], stored["receipts"][-1]


def test_failed_verification_is_returned_to_the_same_session_then_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    streamed, done, receipt = _run_turn(
        tmp_path, monkeypatch,
        [(False, False, "1 failed: assertion details"), (True, False, "2 passed")],
        enabled=True,
    )
    assert streamed["state"] == "passed"
    assert streamed["attempts"] == 2 and streamed["retries"] == 1
    assert len(_RepairAgent.runs) == 2
    repair_prompt, history = _RepairAgent.runs[1]
    assert "Command: pytest -q" in repair_prompt
    assert "assertion details" in repair_prompt
    assert "assertion details" not in str(history)  # diagnostics are supplied as the new observation
    assert "tests and their strength" in repair_prompt
    assert "Turn receipt:" in done["answer"]
    assert "Files: +/- src.py" in done["answer"]
    assert "Verification: pytest -q — passed" in done["answer"]
    assert "Resumptions: 1" in done["answer"]
    assert "Undo: available" in done["answer"]
    assert receipt["changed_files"] == ["+/- src.py"]
    assert receipt["turn_summary"] in done["answer"]
    assert receipt["verified"]["retries"] == 1


def test_repair_write_gate_refuses_test_changes_but_not_initial_turn_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    streamed, _, _ = _run_turn(
        tmp_path, monkeypatch,
        [(False, False, "failure before repair"), (True, False, "2 passed")],
        enabled=True, retry_write_test=True, initial_write_test=True,
    )
    assert streamed["state"] == "passed"
    assert (tmp_path / "workspace" / "tests" / "test_it.py").read_text(encoding="utf-8") == (
        "def test_new():\n    assert True\n"
    )

def test_repair_authorization_question_is_single_use_across_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    streamed, _, _ = _run_turn(
        tmp_path, monkeypatch,
        [(False, False, "failure one"), (False, False, "failure two"),
         (True, False, "3 passed")],
        enabled=True, retry_ask_count=2, retry_write_test=True,
    )
    assert streamed["state"] == "passed"
    assert _RepairAgent.retry_ask_allowed == [True, False]
    assert "CONFLICT:" in _RepairAgent.runs[1][0]


def test_retry_that_removes_a_test_cannot_turn_a_pass_into_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    streamed, done, receipt = _run_turn(
        tmp_path, monkeypatch,
        [(False, False, "failure before repair"), (True, False, "2 passed")],
        enabled=True, retry_test_action="delete",
    )

    assert streamed["state"] == "failed"
    assert streamed["attempts"] == 2 and streamed["retries"] == 1
    assert any(
        flag.startswith("tests_removed_or_skipped: tests/test_it.py")
        for flag in streamed["integrity_flags"]
    )
    assert "a test was removed or skipped to make the check pass" in done["answer"]
    assert "a test was removed or skipped to make the check pass" in receipt["verified"]["output"]
    assert len(_RepairAgent.runs) == 2


def test_retry_that_only_edits_a_test_can_pass_and_records_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    streamed, done, receipt = _run_turn(
        tmp_path, monkeypatch,
        [(False, False, "failure before repair"), (True, False, "2 passed")],
        enabled=True, retry_test_action="edit",
    )

    assert streamed["state"] == "passed"
    assert streamed["integrity_flags"] == ["tests_touched: tests/test_it.py"]
    # With verify_retry on, the answer is the model's text followed by the receipt block (V4).
    assert done["answer"].split("\n\nTurn receipt:", 1)[0] == "repaired"
    assert "\n\nTurn receipt:" in done["answer"]
    assert receipt["verified"]["integrity_flags"] == ["tests_touched: tests/test_it.py"]
    assert len(_RepairAgent.runs) == 2


def test_two_failed_repairs_stop_and_the_answer_does_not_claim_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    streamed, done, receipt = _run_turn(
        tmp_path, monkeypatch,
        [(False, False, "failure one"), (False, False, "failure two"),
         (False, False, "failure three")],
        enabled=True,
    )
    assert streamed["state"] == "failed"
    assert streamed["attempts"] == 3 and streamed["retries"] == 2
    assert len(_RepairAgent.runs) == 3
    assert "Verification failed" in done["answer"]
    assert "failure three" in done["answer"]
    assert "passed" not in done["answer"].lower()
    assert "Files: +/- src.py" in done["answer"]
    assert "Verification: pytest -q — failed" in done["answer"]
    assert "Resumptions: 2" in done["answer"]
    assert receipt["changed_files"] == ["+/- src.py"]
    assert receipt["verified"]["retries"] == 2


def test_abstention_is_explicit_and_does_not_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    streamed, done, receipt = _run_turn(
        tmp_path, monkeypatch, [(True, True, "host execution was not approved")], enabled=True,
    )
    assert streamed["state"] == "abstained" and streamed["attempts"] == 1
    assert streamed["reason"] == "host execution was not approved"
    assert len(_RepairAgent.runs) == 1
    assert "not performed" in done["answer"]
    assert "Verification: pytest -q — abstained (host execution was not approved)" in done["answer"]
    assert receipt["verified"]["state"] == "abstained"


def test_retry_opt_in_off_keeps_the_single_pass_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    streamed, done, receipt = _run_turn(
        tmp_path, monkeypatch, [(False, False, "still failing")], enabled=False,
    )
    assert done["answer"] == "implemented"
    assert "turn_summary" not in done
    assert "changed_files" not in receipt
    assert streamed == {
        "command": "pytest -q",
        "source": "user",
        "state": "failed",
        "output": "still failing",
        "revert_token": streamed["revert_token"],
        "integrity_flags": [],
        "seq": 2,
    }
    assert streamed["revert_token"]
    assert receipt["verified"] == {
        "command": "pytest -q",
        "source": "user",
        "state": "failed",
        "output": "still failing",
        "integrity_flags": [],
    }
    assert len(_RepairAgent.runs) == 1


def test_false_model_claim_is_contradicted_by_failed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, done, receipt = _run_turn(
        tmp_path, monkeypatch,
        [(False, False, "still failing")] * 3,
        enabled=True,
        answers=["Tests passed; implementation complete."],
    )
    assert done["answer"].startswith("Tests passed; implementation complete.")
    assert "the check did not pass: failed" in done["answer"]
    assert receipt["verified"]["state"] == "failed"


def test_repair_can_end_in_explicit_conflict_without_claiming_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    streamed, done, receipt = _run_turn(
        tmp_path, monkeypatch,
        [(False, False, "failure details")], enabled=True, conflict=True,
    )
    assert streamed["state"] == "conflict"
    assert streamed["attempts"] == 1 and streamed["retries"] == 0
    assert "Verification: pytest -q — conflict" in done["answer"]
    assert "requested behavior contradicts the existing test" in streamed["output"]
    assert receipt["verified"]["state"] == "conflict"
    assert receipt["verified"]["conflict_reason"] == "the requested behavior contradicts the existing test."
    assert len(_RepairAgent.runs) == 2


def test_empty_conflict_marker_does_not_end_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    streamed, _, _ = _run_turn(
        tmp_path, monkeypatch,
        [(False, False, "failure one"), (True, False, "2 passed")],
        enabled=True, empty_conflict=True,
    )
    assert streamed["state"] == "passed"
    assert streamed["attempts"] == 2
    assert len(_RepairAgent.runs) == 2


def test_disabled_abstention_preserves_the_legacy_answer_event_and_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    streamed, done, receipt = _run_turn(
        tmp_path, monkeypatch,
        [(True, True, "host execution was not approved")], enabled=False,
    )

    # Before opt-in retries, abstention only appeared as the verifier's state/output; it did not
    # rewrite the model's answer or add retry-specific fields to the event or stored verdict.
    assert done["answer"] == "implemented"
    assert streamed == {
        "command": "pytest -q",
        "source": "user",
        "state": "abstained",
        "output": "host execution was not approved",
        "revert_token": streamed["revert_token"],
        "integrity_flags": [],
        "seq": 2,
    }
    assert receipt["verified"] == {
        "command": "pytest -q",
        "source": "user",
        "state": "abstained",
        "output": "host execution was not approved",
        "integrity_flags": [],
    }
    assert len(_RepairAgent.runs) == 1
