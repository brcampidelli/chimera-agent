"""S30-61, Amendment 5: every wire record carries the kind of call and the run, declared by the caller,
and reconciliation is per run. Offline: the real Agent and LLMGateway against a fake provider.

Before the fix, the closing call (`Agent._close`) and the compaction summariser were gateway calls
that never became a `StepRecord`, so an unmutated run reconciled as dirty. These tests pin that a
clean run with both reconciles with zero discrepancies, that the three registered step faults are
still found, and how a record that lies about its kind is caught.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.governance import wire_context
from chimera.governance.reconcile import append_wire_record, digest, reconcile

MODEL = "ollama_chat/synthetic"
SECRET = "Bearer synthetic-secret-do-not-log"
THRESHOLD = 2000


class Provider:
    """Stands in for `litellm.completion`: one `read_file` per step, a prompt that grows 1,100 tokens a
    step (so compaction fires from step 2), and plain text for every tool-free call."""

    def __init__(self, *, fail_at: int | None = None) -> None:
        from chimera.core.summarise import SYSTEM

        self.summary_system, self.calls, self.steps, self.fail_at = SYSTEM, 0, 0, fail_at

    def __call__(self, **kwargs: Any) -> Any:
        self.calls += 1
        if self.fail_at is not None and self.calls >= self.fail_at:
            raise RuntimeError("provider down")
        usage = SimpleNamespace(prompt_tokens=1100 * max(self.steps, 1), completion_tokens=5)
        if not kwargs.get("tools"):
            first = kwargs["messages"][0].get("content")
            text = "Binding: read the notes." if first == self.summary_system else f"Final answer. {SECRET}"
            message = SimpleNamespace(content=text, tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=usage)
        self.steps += 1
        usage.prompt_tokens = 1100 * self.steps
        fn = SimpleNamespace(name="read_file", arguments=json.dumps({"path": f"notes{self.steps}.txt"}))
        message = SimpleNamespace(content="", tool_calls=[SimpleNamespace(id=f"c{self.steps}", function=fn)])
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls")], usage=usage)


def _agent_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, wire: bool = True, max_steps: int = 4,
               summarise: bool = True, fail_at: int | None = None, home: Path | None = None) -> tuple[Any, Path]:
    import litellm

    import chimera.core.context_budget as cb
    from chimera.config import Settings
    from chimera.core.agent import Agent, AgentConfig
    from chimera.providers.gateway import LLMGateway
    from chimera.tools.files import ReadFileTool
    from chimera.tools.registry import ToolRegistry

    monkeypatch.setattr(litellm, "completion", Provider(fail_at=fail_at))
    monkeypatch.setattr(cb, "window_tokens", lambda _model: 10_000)
    home = home or tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    for k in range(1, 10):  # a different file each step, so the loop breaker never trips
        (workspace / f"notes{k}.txt").write_text(f"some notes {SECRET}\n" * 20, encoding="utf-8")
    settings = Settings(_env_file=None, CHIMERA_HOME=str(home), CHIMERA_WIRE_LOG=wire, CHIMERA_CACHE=False)  # type: ignore[call-arg]
    registry = ToolRegistry()
    registry.register(ReadFileTool(workspace))
    fraction = (math.ceil(THRESHOLD / cb.DEFAULT_TRIGGER) + 0.5) / 10_000
    agent = Agent(LLMGateway(settings=settings), registry, AgentConfig(
        model=MODEL, max_steps=max_steps, project_root=workspace, trace_path=home / "traces.jsonl",
        context_budget=fraction, keep_recent=2, summarise_compaction=summarise,
    ))
    assert agent._budget is not None and agent._budget.threshold == THRESHOLD
    return agent.run("Read the notes files one by one."), home


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _rewrite_trace(home: Path, edit: Any) -> None:
    trace = home / "traces.jsonl"
    rows = _rows(trace)
    edit(rows[0])
    trace.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


# --------------------------------------------------------------------------------- a clean run


def test_a_clean_run_with_a_closing_call_and_summary_compaction_reconciles_with_zero_discrepancies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, home = _agent_run(tmp_path, monkeypatch)
    assert result.stopped_reason == "max_steps"
    wire = _rows(home / "wire.jsonl")
    kinds = [row["kind"] for row in wire]
    # The kinds are what the CALLERS declared: four steps, a summary after each compacting step, and
    # the closing call at max_steps.
    assert kinds.count("step") == 4 and kinds.count("summary") >= 1 and kinds[-1] == "close"
    assert {row["run_id"] for row in wire} == {result.run_id} and all(row["traced"] for row in wire)
    trace = _rows(home / "traces.jsonl")[0]
    assert trace["run_id"] == result.run_id
    assert sorted(c["kind"] for c in trace["side_calls"]) == sorted(k for k in kinds if k != "step")
    audit = reconcile(home / "wire.jsonl", home / "traces.jsonl")
    assert audit["clean"], audit
    assert audit["by_kind"] == {"close": 1, "step": 4, "summary": kinds.count("summary")}
    assert audit["per_run"][result.run_id]["clean"] and audit["per_run"][result.run_id]["discrepancies"] == 0


def test_neither_the_wire_log_nor_the_trace_claims_hold_a_body_or_a_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, home = _agent_run(tmp_path, monkeypatch)
    raw = (home / "wire.jsonl").read_text(encoding="utf-8")
    assert SECRET not in raw and "synthetic-secret" not in raw and "notes" not in raw and "Final answer" not in raw
    allowed = {"wire_id", "model", "request_digest", "response_digest", "ts", "kind", "run_id", "traced"}
    assert all(set(row) == allowed for row in _rows(home / "wire.jsonl"))
    claims = _rows(home / "traces.jsonl")[0]["side_calls"]
    assert all(set(c) == {"kind", "wire_id", "request_digest", "response_digest", "at_step"} for c in claims)
    assert SECRET not in json.dumps(claims)


def test_off_by_default_nothing_is_written_and_the_trace_has_no_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.config import Settings

    assert Settings(_env_file=None).wire_log is False
    _, home = _agent_run(tmp_path, monkeypatch, wire=False)
    assert not (home / "wire.jsonl").exists()
    assert "side_calls" not in _rows(home / "traces.jsonl")[0]


# ------------------------------------------------------------------- the step faults, still found


def test_omission_fabrication_and_altered_copy_of_a_step_are_still_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, home = _agent_run(tmp_path, monkeypatch)
    original = (home / "traces.jsonl").read_text(encoding="utf-8")
    steps = _rows(home / "traces.jsonl")[0]["steps"]
    victim = steps[1]["wire_id"]

    _rewrite_trace(home, lambda row: row["steps"].pop(1))
    audit = reconcile(home / "wire.jsonl", home / "traces.jsonl")
    assert audit["missing_steplog"] == [victim] and not audit["clean"]

    (home / "traces.jsonl").write_text(original, encoding="utf-8")
    made_up = {"index": 9, "wire_id": "f" * 32, "request_digest": digest("q"), "response_digest": digest("r")}
    _rewrite_trace(home, lambda row: row["steps"].append(made_up))
    assert reconcile(home / "wire.jsonl", home / "traces.jsonl")["missing_wire"] == ["f" * 32]

    (home / "traces.jsonl").write_text(original, encoding="utf-8")
    _rewrite_trace(home, lambda row: row["steps"][2].update(response_digest=digest("other")))
    assert reconcile(home / "wire.jsonl", home / "traces.jsonl")["altered"] == [steps[2]["wire_id"]]


def test_a_dropped_claim_or_a_claim_that_does_not_fit_the_trace_is_a_discrepancy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, home = _agent_run(tmp_path, monkeypatch)
    original = (home / "traces.jsonl").read_text(encoding="utf-8")
    close_id = next(r["wire_id"] for r in _rows(home / "wire.jsonl") if r["kind"] == "close")

    _rewrite_trace(home, lambda row: row.update(side_calls=[c for c in row["side_calls"] if c["kind"] != "close"]))
    assert reconcile(home / "wire.jsonl", home / "traces.jsonl")["unaccounted"] == [close_id]

    (home / "traces.jsonl").write_text(original, encoding="utf-8")
    _rewrite_trace(home, lambda row: row.update(stopped_reason="final"))  # a closing call in a run that ended on its own
    audit = reconcile(home / "wire.jsonl", home / "traces.jsonl")
    assert [e["wire_id"] for e in audit["implausible"]] == [close_id] and not audit["clean"]

    (home / "traces.jsonl").write_text(original, encoding="utf-8")
    _rewrite_trace(home, lambda row: row["side_calls"].append(
        {"kind": "summary", "wire_id": "e" * 32, "request_digest": "q", "response_digest": "r", "at_step": 2}))
    assert reconcile(home / "wire.jsonl", home / "traces.jsonl")["side_calls_without_wire"] == ["e" * 32]


# ------------------------------------------------------------------------ the forged-kind attack


def test_a_forged_summary_record_cannot_cover_a_fabricated_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The attack: fabricate a step in the trace and append a wire record for it that claims kind
    `summary`, hoping the reconciler treats non-step records as "expected, not compared". A step is
    only ever witnessed by a `step` record, so the forged record is reported as `kind_mismatch`."""
    result, home = _agent_run(tmp_path, monkeypatch)
    q, r = digest("fabricated request"), digest("fabricated response")
    forged = append_wire_record(home / "wire.jsonl", model=MODEL, request_digest=q, response_digest=r,
                                kind="summary", run_id=result.run_id, traced=True)
    _rewrite_trace(home, lambda row: row["steps"].append(
        {"index": 5, "wire_id": forged, "request_digest": q, "response_digest": r}))
    audit = reconcile(home / "wire.jsonl", home / "traces.jsonl")
    assert audit["kind_mismatch"] == [forged] and not audit["clean"]
    assert not audit["missing_wire"]  # without the kind it would have passed as a witnessed step


def test_relabelling_a_real_step_as_a_summary_needs_a_compaction_the_trace_does_not_have(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction: hide a real step by deleting it from the trace. Its wire record says
    `step`, and a `step` record can only be matched by a StepRecord — a claim cannot absorb it."""
    _, home = _agent_run(tmp_path, monkeypatch, summarise=False)
    row = _rows(home / "traces.jsonl")[0]
    hidden = row["steps"][0]
    _rewrite_trace(home, lambda r: (r["steps"].pop(0), r.setdefault("side_calls", []).append(
        {"kind": "step", "wire_id": hidden["wire_id"], "request_digest": hidden["request_digest"],
         "response_digest": hidden["response_digest"], "at_step": 1})))
    assert reconcile(home / "wire.jsonl", home / "traces.jsonl")["missing_steplog"] == [hidden["wire_id"]]
    # Even rewriting the wire line (outside the append-only model) to say `summary` leaves the claim
    # to explain: step 1 never compacted, so a summary there does not fit the run's own trace.
    wire = home / "wire.jsonl"
    rows = _rows(wire)
    for record in rows:
        if record["wire_id"] == hidden["wire_id"]:
            record["kind"] = "summary"
    wire.write_text("".join(json.dumps(record) + "\n" for record in rows), encoding="utf-8")
    _rewrite_trace(home, lambda r: r["side_calls"][-1].update(kind="summary"))
    audit = reconcile(wire, home / "traces.jsonl")
    assert [(e["wire_id"], e["kind"]) for e in audit["implausible"]] == [(hidden["wire_id"], "summary")]
    assert not audit["clean"]


# ---------------------------------------------------------------------------- per-run, shared file


def test_two_interleaved_runs_in_one_wire_file_reconcile_per_run(tmp_path: Path) -> None:
    wire, trace = tmp_path / "wire.jsonl", tmp_path / "traces.jsonl"
    steps: dict[str, list[dict[str, Any]]] = {"run-a": [], "run-b": []}
    for index in range(3):
        for run in ("run-a", "run-b"):  # interleaved, as two cron jobs sharing CHIMERA_HOME would be
            q, r = digest([run, index]), digest([run, index, "reply"])
            wid = append_wire_record(wire, model=MODEL, request_digest=q, response_digest=r,
                                     kind="step", run_id=run, traced=True)
            steps[run].append({"index": index + 1, "wire_id": wid, "request_digest": q, "response_digest": r})
    append_wire_record(wire, model=MODEL, request_digest="x", response_digest="y", kind="undeclared")  # a judge
    append_wire_record(wire, model=MODEL, request_digest="x", response_digest="z", kind="step",
                       run_id="run-c", traced=False)  # a run that writes no trace, and said so
    lines = [{"run_id": run, "ts": "2000-01-01T00:00:00+00:00", "stopped_reason": "final", "steps": s}
             for run, s in steps.items()]
    trace.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    audit = reconcile(wire, trace)
    assert audit["clean"], audit
    assert set(audit["per_run"]) == {"run-a", "run-b"}
    assert audit["outside_runs"] == {"undeclared": 1} and audit["untraced_runs"] == {"run-c": {"step": 1}}
    # Moving one run's step into the other run's trace is caught, though both records exist.
    moved = [dict(lines[0], steps=[*steps["run-a"], steps["run-b"][0]]), dict(lines[1], steps=steps["run-b"][1:])]
    trace.write_text("".join(json.dumps(line) + "\n" for line in moved), encoding="utf-8")
    audit = reconcile(wire, trace)
    assert audit["run_mismatch"] == [steps["run-b"][0]["wire_id"]]
    assert audit["per_run"]["run-a"]["discrepancies"] == 1 and audit["per_run"]["run-b"]["clean"]
    # One run, read on its own.
    trace.write_text(json.dumps(lines[1]) + "\n", encoding="utf-8")
    assert reconcile(wire, trace, run_id="run-b")["clean"]
    whole = reconcile(wire, trace)
    assert whole["missing_runs"] == {"run-a": {"step": 3}} and not whole["clean"]


def test_a_run_that_raised_is_its_own_reported_category(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError):
        _agent_run(tmp_path, monkeypatch, fail_at=3)
    home = tmp_path / "home"
    records = _rows(home / "wire.jsonl")
    assert len(records) == 2 and not (home / "traces.jsonl").exists()
    audit = reconcile(home / "wire.jsonl", home / "traces.jsonl")
    assert audit["missing_runs"] == {records[0]["run_id"]: {"step": 2}}
    assert audit["missing_steplog"] == [] and not audit["clean"]  # reported as a run, not as two lost steps


def test_records_older_than_every_trace_line_are_read_as_rotated_out_not_missing(tmp_path: Path) -> None:
    wire, trace = tmp_path / "wire.jsonl", tmp_path / "traces.jsonl"
    wire.write_text(json.dumps({"wire_id": "old", "kind": "step", "run_id": "gone", "traced": True,
                                "request_digest": "q", "response_digest": "r", "ts": "2026-01-01T00:00:00+00:00"})
                    + "\n", encoding="utf-8")
    trace.write_text(json.dumps({"run_id": "kept", "ts": "2026-10-08T00:00:00+00:00", "steps": []}) + "\n",
                     encoding="utf-8")
    audit = reconcile(wire, trace)
    assert audit["before_trace_window"] == {"gone": {"step": 1}} and audit["missing_runs"] == {}
    assert audit["clean"]


def test_the_rotated_trace_generation_is_read_too(tmp_path: Path) -> None:
    wire, trace = tmp_path / "wire.jsonl", tmp_path / "traces.jsonl"
    wid = append_wire_record(wire, model=MODEL, request_digest="q", response_digest="r", kind="step",
                             run_id="r1", traced=True)
    step = {"index": 1, "wire_id": wid, "request_digest": "q", "response_digest": "r"}
    (tmp_path / "traces.jsonl.1").write_text(json.dumps({"run_id": "r1", "steps": [step]}) + "\n", encoding="utf-8")
    trace.write_text("", encoding="utf-8")
    assert reconcile(wire, trace)["clean"]


# ------------------------------------------------------------------------ old records, the context


def test_records_written_before_kinds_existed_stay_readable_and_are_said_to_be_unknown(tmp_path: Path) -> None:
    wire, trace = tmp_path / "wire.jsonl", tmp_path / "traces.jsonl"
    old = append_wire_record(wire, model=MODEL, request_digest="q", response_digest="r")
    lost = append_wire_record(wire, model=MODEL, request_digest="q2", response_digest="r2")
    trace.write_text(json.dumps({"steps": [{"index": 1, "wire_id": old, "request_digest": "q",
                                            "response_digest": "r"}]}) + "\n", encoding="utf-8")
    audit = reconcile(wire, trace)
    assert audit["legacy_records"] == 2 and audit["by_kind"] == {"unknown": 2}
    assert "before S30-61 Amendment 5" in audit["note"]
    assert audit["missing_steplog"] == [lost]  # compared as a step, exactly as before the fix


def test_tools_that_run_together_on_worker_threads_still_belong_to_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker thread starts with an empty context: without the copy, a model call a parallel read
    made would land outside the run and its record would be reported as `outside_runs`."""
    import litellm

    from chimera.core.agent import Agent, AgentConfig
    from chimera.tools.files import ReadFileTool
    from chimera.tools.registry import ToolRegistry

    seen: list[tuple[str | None, str]] = []

    class Watching(ReadFileTool):
        def run(self, **kwargs: Any) -> str:
            scope, kind = wire_context.current()
            seen.append((scope.run_id if scope is not None else None, kind))
            return str(super().run(**kwargs))

    def provider(**kwargs: Any) -> Any:
        if any(m.get("role") == "tool" for m in kwargs["messages"]):
            message = SimpleNamespace(content="done", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)
        calls = [SimpleNamespace(id=f"c{i}", function=SimpleNamespace(
            name="read_file", arguments=json.dumps({"path": f"n{i}.txt"}))) for i in (1, 2)]
        message = SimpleNamespace(content="", tool_calls=calls)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls")], usage=None)

    monkeypatch.setattr(litellm, "completion", provider)
    for i in (1, 2):
        (tmp_path / f"n{i}.txt").write_text("x", encoding="utf-8")
    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    registry = ToolRegistry()
    registry.register(Watching(tmp_path))
    gateway = LLMGateway(settings=Settings(_env_file=None, CHIMERA_HOME=str(tmp_path / "h")))  # type: ignore[call-arg]
    result = Agent(gateway, registry, AgentConfig(model=MODEL, max_steps=3, project_root=tmp_path,
                                                  trace_path=tmp_path / "t.jsonl")).run("read both")
    assert result.steplog.steps[0].ran_together == 2  # the two reads really ran on worker threads
    assert seen == [(result.run_id, "tool"), (result.run_id, "tool")]


def test_the_kind_is_the_callers_and_a_nested_run_does_not_inherit_it() -> None:
    assert wire_context.current() == (None, wire_context.UNDECLARED)
    with wire_context.wire_run("outer", traced=True) as outer, wire_context.wire_kind(wire_context.TOOL):
        assert wire_context.current() == (outer, "tool")
        with wire_context.wire_run("inner", traced=False) as inner:
            assert wire_context.current() == (inner, wire_context.UNDECLARED)
            with wire_context.wire_kind(wire_context.STEP):
                assert wire_context.current()[1] == "step"
        assert wire_context.current() == (outer, "tool")
    assert wire_context.current() == (None, wire_context.UNDECLARED)
