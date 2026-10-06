from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_census_same_turn_and_categories(tmp_path: Path) -> None:
    census = _load("empty_census", ROOT / "bench/empty_commitments/census.py")
    fixture = tmp_path / "fixture.jsonl"
    fixture.write_text("\n".join([
        json.dumps({"role": "assistant", "content": "I'll remind you tomorrow.", "tool_calls": []}),
        json.dumps({"role": "assistant", "content": "I'll remind you tomorrow.", "tool_calls": [{"name": "schedule_once", "result": {"ok": False}}]}),
        json.dumps({"role": "assistant", "content": "I'll follow up later." , "tool_calls": [{"name": "schedule_once", "result": {"ok": True}}]}),
        json.dumps({"role": "assistant", "content": "Não posso agendar isso."}),
        json.dumps({"role": "user", "content": "I'll remind you tomorrow."}),
    ]) + "\n", encoding="utf-8")
    result = census.classify(census.read_records(fixture))
    assert result["assistant_replies"] == 4
    assert result["counts"] == {"empty": 1, "false_claim": 1, "unanchored": 0, "over_refusal": 1}


def test_schedule_once_tool_requires_approval_and_persists(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from chimera.tools.schedule_once import ScheduleOnceTool

    approvals: list[str] = []
    current = datetime.now(UTC)
    tool = ScheduleOnceTool(
        home=tmp_path, workspace=tmp_path, now=lambda: current.timestamp(),
        approve=lambda action, reason: approvals.append(action) is None,
    )
    result = tool.run(run_at=(current + timedelta(hours=1)).isoformat(), task="call dentist")
    assert result.startswith("schedule created:")
    assert approvals and (tmp_path / "scheduler" / "jobs.json").exists()

    denied = ScheduleOnceTool(
        home=tmp_path / "denied", workspace=tmp_path, now=lambda: current.timestamp(),
        approve=lambda _action, _reason: False,
    )
    result = denied.run(run_at=(current + timedelta(hours=1)).isoformat(), task="call dentist")
    assert "approval was not granted" in result
    assert not (tmp_path / "denied" / "scheduler" / "jobs.json").exists()


def test_fake_backend_runs_each_fixed_request_in_three_arms() -> None:
    harness = _load("empty_arms", ROOT / "bench/empty_commitments/run_arms.py")

    class FakeBackend:
        def reply(self, prompt: str, *, arm: str, tool_enabled: bool):
            return f"{arm}:{prompt}", [{"tool": "schedule_once"}] if tool_enabled else []

    rows = harness.run([{"id": "x", "lang": "en", "family": "explicit", "request": "Remind me tomorrow."}], FakeBackend())
    assert [row["arm"] for row in rows] == ["A", "B", "C"]
    assert rows[0]["events"] == rows[1]["events"] == []
    assert rows[2]["events"] == [{"tool": "schedule_once"}]
