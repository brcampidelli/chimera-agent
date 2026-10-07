from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from chimera.cli.main import _exit_for, app
from chimera.config import get_settings

runner = CliRunner()


def test_stop_reason_exit_codes_are_distinct_and_documentable() -> None:
    expected = {
        "final": 0, "max_steps": 2, "tool_loop": 3, "budget": 4, "spend": 5,
        "cancelled": 6, "context_stuck": 7, "handover": 8, "exhausted": 9,
        "paused": 10, "denied": 11, "unknown-reason": 12,
    }
    assert {reason: _exit_for(reason) for reason in expected} == expected


def test_json_payload_matches_documented_schema_and_preserves_human_output(monkeypatch: Any) -> None:
    from chimera.providers import LLMGateway

    monkeypatch.setattr(LLMGateway, "quick", lambda *a, **k: "answer [/]")
    plain = runner.invoke(app, ["run", "task"])
    machine = runner.invoke(app, ["run", "task", "--json"])
    schema = {
        "type": "object", "required": ["answer", "stopped_reason", "receipt"],
        "additionalProperties": False,
        "properties": {
            "answer": {"type": "string"}, "stopped_reason": {"type": "string"},
            "receipt": {"type": "object", "required": ["model", "usd", "tokens", "steps"],
                        "additionalProperties": False,
                        "properties": {"model": {"type": "string"},
                                       "usd": {"type": ["number", "null"]},
                                       "tokens": {"type": "integer"}, "steps": {"type": "integer"}}},
        },
    }
    try:
        import jsonschema
    except ImportError:
        data = json.loads(machine.stdout)
        assert set(data) == set(schema["required"])
        assert set(data["receipt"]) == set(schema["properties"]["receipt"]["required"])
    else:
        jsonschema.validate(json.loads(machine.stdout), schema)
    assert "answer [/]" in plain.stdout
    assert machine.stdout.endswith("\n") and "answer" in json.loads(machine.stdout)["answer"]
    assert machine.stderr == ""


def test_run_reads_stdin_for_dash_and_absent_prompt(monkeypatch: Any) -> None:
    seen: list[str] = []
    monkeypatch.setattr("chimera.providers.LLMGateway.quick", lambda self, prompt, **kwargs: seen.append(prompt) or "ok")
    for args in (["run", "-"], ["run"]):
        result = runner.invoke(app, args, input="stdin task\n")
        assert result.exit_code == 0
    assert seen == ["stdin task", "stdin task"]


def test_agent_json_uses_stdin_and_emits_one_event(monkeypatch: Any, tmp_path: Path) -> None:
    class StubAgent:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def run(self, task: str, **_kwargs: Any) -> Any:
            assert task == "stdin task"
            return type("Result", (), {"answer": "done", "stopped_reason": "final", "model": "stub",
                                        "usd": 0.1, "prompt_tokens": 2, "completion_tokens": 3,
                                        "steps": 1, "tool_calls_made": 0})()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setenv("CHIMERA_SANDBOX", "docker")
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    monkeypatch.setattr("chimera.core.Agent", StubAgent)
    monkeypatch.setattr("chimera.providers.LLMGateway", lambda: object())
    try:
        result = runner.invoke(app, ["agent", "-", "--jsonl"], input="stdin task\n")
        assert result.exit_code == 0
        assert len(result.stdout.splitlines()) == 1, result.stdout
        line = result.stdout.splitlines()[0]
        event = json.loads(line)
        assert event["kind"] == "final"
        assert event["data"]["stopped_reason"] == "final"
        assert event["data"]["receipt"] == {"model": "stub", "usd": 0.1, "tokens": 5, "steps": 1}
    finally:
        get_settings.cache_clear()


def test_jsonl_reuses_core_final_event(capsys: Any) -> None:
    from chimera.cli.main import _emit_headless
    from chimera.core.events import final

    payload = {"answer": "done", "stopped_reason": "final",
               "receipt": {"model": "m", "usd": None, "tokens": 0, "steps": 0}}
    _emit_headless(payload, json_output=False, jsonl=True)
    output = capsys.readouterr().out
    event = json.loads(output)
    assert event["kind"] == final(True, "done").kind
    assert event["data"]["answer"] == "done"
    assert output.count("\n") == 1


def test_exit_code_table_is_in_command_reference() -> None:
    reference = (Path(__file__).resolve().parents[1] / "docs" / "commands.md").read_text(encoding="utf-8")
    assert "Headless output exit codes" in reference
    for reason in ("max_steps", "tool_loop", "budget", "spend", "cancelled", "context_stuck", "handover", "exhausted", "paused", "denied"):
        assert reason in reference


def _solve_harness(monkeypatch: Any, tmp_path: Path, ending: str, success: bool) -> None:
    """``solve`` with the model and the loop replaced; the stub prints a human line mid-run."""
    import chimera.core as core
    import chimera.providers as providers
    import chimera.tools as tools
    from chimera.cli import main as cli
    from chimera.core import AutonomousResult

    class _Auto:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def run(self, task: str, **_k: Any) -> AutonomousResult:
            cli.console.print("human progress line")
            return AutonomousResult(answer=f"did {task}", success=success, ending=ending)  # type: ignore[arg-type]  # ending is a Literal and the parametrised values are its members

        def run_tainted(self) -> bool:
            # `solve` reads the run's taint from the agent after `run` (S30-25), so the stand-in
            # answers it as a run that read nothing untrusted would.
            return False

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("CHIMERA_APPROVAL_MODE", "deny")
    get_settings.cache_clear()
    monkeypatch.setattr(core, "Agent", lambda *a, **k: object())
    monkeypatch.setattr(core, "AutonomousAgent", _Auto)
    monkeypatch.setattr(core, "Planner", lambda *a, **k: None)
    monkeypatch.setattr(core, "Manager", lambda *a, **k: None)
    monkeypatch.setattr(core, "WorkspaceGuard", lambda *a, **k: None)
    monkeypatch.setattr(providers, "LLMGateway", lambda *a, **k: object())
    monkeypatch.setattr(tools, "default_registry", lambda ws, **k: object())


@pytest.mark.parametrize(
    ("ending", "success", "code", "reason"),
    [("success", True, 0, "final"), ("exhausted", False, 9, "exhausted"), ("handover", False, 8, "handover")],
)
def test_solve_json_is_the_only_thing_on_stdout(
    monkeypatch: Any, tmp_path: Path, ending: str, success: bool, code: int, reason: str
) -> None:
    """The commit printed the JSON and then fell through to the human answer and cost line on
    success, so stdout held one JSON object followed by prose; and every banner printed during the
    run landed on stdout too."""
    _solve_harness(monkeypatch, tmp_path, ending, success)
    try:
        result = runner.invoke(app, ["solve", "fix it", "-w", str(tmp_path), "--json"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == code, result.output
    payload = json.loads(result.stdout)  # one object and nothing else, or this raises
    assert payload["stopped_reason"] == reason
    assert "human progress line" in result.stderr


def test_solve_without_json_still_exits_1_for_an_unfinished_run(monkeypatch: Any, tmp_path: Path) -> None:
    """Existing callers read 1 as "did not finish"; the headless table must not change that."""
    _solve_harness(monkeypatch, tmp_path, "exhausted", False)
    try:
        result = runner.invoke(app, ["solve", "fix it", "-w", str(tmp_path)])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 1


def test_a_terminal_on_stdin_is_refused_not_waited_on(monkeypatch: Any) -> None:
    import io

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

        def read(self, *_a: Any) -> str:
            raise AssertionError("read a terminal")

    monkeypatch.setattr("sys.stdin", _Tty())
    from chimera.cli.main import _task_from_stdin

    with pytest.raises(typer.Exit) as exc:
        _task_from_stdin("-")
    assert exc.value.exit_code == 1  # not 2, which is max_steps in the table


def test_piped_chat_is_still_the_conversation() -> None:
    """The commit made `chat` one-shot whenever stdin was not a terminal, which turned
    `printf 'hi\n/exit\n' | chimera chat` into a single prompt, and ran it on an ungoverned
    registry. One-shot runs belong to `agent`."""
    params = {p.name for p in typer.main.get_command(app).commands["chat"].params}  # type: ignore[attr-defined]  # a Typer app's root command is a Click group
    assert not {"prompt", "json_output", "jsonl"} & params


def test_documented_table_is_the_code_table() -> None:
    from chimera.cli.main import _STOP_EXIT_CODES

    reference = (Path(__file__).resolve().parents[1] / "docs" / "commands.md").read_text(encoding="utf-8")
    for reason, code in _STOP_EXIT_CODES.items():
        assert f"| `{reason}` | `{code}` |" in reference, reason
