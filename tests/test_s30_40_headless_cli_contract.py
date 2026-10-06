from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
