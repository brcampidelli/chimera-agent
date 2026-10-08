"""S30-61 Amendment 3, offline: the long-run runner drives the real Agent and LLMGateway against a fake
provider (no model, no network, no GPU), and every registered mechanism is shown to act — and to be
attributed — before a single real call is spent on it."""
from __future__ import annotations

import importlib.util
import json
import random
from collections import Counter
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]


def _load(name: str, path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    return _load("wire_long", "bench/wire_reconcile/run_long.py")


class FakeProvider:
    """Stands in for `litellm.completion`: reads the chain one file per step, writes, then answers.

    The prompt count grows 450 tokens per step, so the 2,500-token threshold is crossed at step 5."""

    def __init__(self, chain: list[str], *, stream_seen: list[bool] | None = None) -> None:
        from chimera.core.summarise import SYSTEM

        self.chain, self.summary_system, self.step_calls = chain, SYSTEM, 0
        self.models: list[str] = []
        self.streamed: list[bool] = stream_seen if stream_seen is not None else []

    def __call__(self, **kwargs: Any) -> Any:
        self.models.append(kwargs["model"])
        self.streamed.append(bool(kwargs.get("stream")))
        if not kwargs.get("tools"):
            first = kwargs["messages"][0].get("content")
            text = "Binding facts: the chain is being read." if first == self.summary_system else "Final: 42."
            return self._reply(kwargs, text=text, call=None, prompt=1200)
        self.step_calls += 1
        index = self.step_calls
        if index <= len(self.chain):
            call = ("read_file", {"path": self.chain[index - 1]})
        elif index == len(self.chain) + 1:
            call = ("write_file", {"path": "total.txt", "content": "42\n"})
        else:
            call = None
        return self._reply(kwargs, text="" if call else "The sum is 42.", call=call, prompt=900 + 450 * (index - 1))

    def _reply(self, kwargs: dict[str, Any], *, text: str, call: tuple[str, dict[str, Any]] | None,
               prompt: int) -> Any:
        usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=12)
        finish = "tool_calls" if call else "stop"
        if kwargs.get("stream"):
            return self._chunks(text, call, usage, finish)
        tool_calls = None
        if call:
            fn = SimpleNamespace(name=call[0], arguments=json.dumps(call[1]))
            tool_calls = [SimpleNamespace(id=f"c{self.step_calls}", function=fn)]
        message = SimpleNamespace(content=text, tool_calls=tool_calls)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)], usage=usage,
                               id=f"gen-{len(self.models)}")

    def _chunks(self, text: str, call: tuple[str, dict[str, Any]] | None, usage: Any, finish: str) -> Any:
        deltas = None
        if call:
            fn = SimpleNamespace(name=call[0], arguments=json.dumps(call[1]))
            deltas = [SimpleNamespace(index=0, id=f"c{self.step_calls}", function=fn)]
        delta = SimpleNamespace(content=text or None, tool_calls=deltas)
        yield SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None, id="gen-s")
        end = SimpleNamespace(content=None, tool_calls=None)
        yield SimpleNamespace(choices=[SimpleNamespace(delta=end, finish_reason=finish)], usage=usage, id="gen-s")


def _run(runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, arm: str, *,
         max_steps: int = 15, name: str | None = None) -> tuple[dict[str, Any], FakeProvider, Path]:
    import litellm

    fake = FakeProvider(runner.chain(0))
    monkeypatch.setattr(litellm, "completion", fake)
    run_dir = tmp_path / (name or runner.run_name(0, 0, arm))
    meta = runner.generate_one(run_dir, 0, arm, {"calls": 0}, None, max_steps=max_steps)
    return meta, fake, run_dir


def _reconcile(run_dir: Path) -> dict[str, Any]:
    from chimera.governance.reconcile import reconcile

    return reconcile(run_dir / "home" / "wire.jsonl", run_dir / "home" / "traces.jsonl")


# ------------------------------------------------------------------------------------------- design


def test_the_plan_is_the_registered_hundred_runs_and_arm_split(runner: ModuleType) -> None:
    entries = runner.plan()
    assert len(entries) == 100 and len(set(entries)) == 100
    assert Counter(arm for *_, arm in entries) == {"S": 25, "M": 26, "F": 25, "T": 24}
    assert [arm for *_, arm in runner.smoke_plan()] == ["F", "T"]
    assert all(entry in entries for entry in runner.smoke_plan())


def test_every_task_is_a_chain_that_cannot_be_read_without_following_it(runner: ModuleType, tmp_path: Path) -> None:
    for task_index, (_, length, _) in enumerate(runner.TASKS):
        workspace = tmp_path / str(task_index)
        workspace.mkdir()
        runner.seed_workspace(task_index, workspace)
        names = runner.chain(task_index)
        assert len(names) == length and 6 <= length <= 8 and names[0] == "start.txt"
        for here, there in zip(names, [*names[1:], None], strict=True):
            text = (workspace / here).read_text(encoding="utf-8")
            assert 1200 <= len(text) <= 1800
            assert (f"Next file: {there}" if there else "Next file: none") in text
        decoys = {p.name for p in workspace.iterdir()} - set(names)
        assert len(decoys) == 3  # reading "everything" is wrong, so the chain is the only route
        assert "start.txt" in runner.task_text(task_index)
    # deterministic: the same task seeds the same bytes
    again = tmp_path / "again"
    again.mkdir()
    runner.seed_workspace(0, again)
    assert all((again / n).read_bytes() == (tmp_path / "0" / n).read_bytes() for n in runner.chain(0))


@pytest.mark.parametrize("window", [32_768, 40_960, 131_072, 262_144])
def test_the_fraction_puts_the_agents_own_threshold_at_the_registered_tokens(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, window: int
) -> None:
    import chimera.core.context_budget as cb

    monkeypatch.setattr(cb, "window_tokens", lambda _model: window)
    budget = cb.ContextBudget.for_model(runner.MODEL, fraction=runner.compaction_fraction(runner.MODEL))
    assert budget.threshold == runner.THRESHOLD == 2500


def test_mutations_are_amendment_ones_draw_for_draw(runner: ModuleType) -> None:
    original = _load("wire_ollama_for_long", "bench/wire_reconcile/run_ollama.py")
    steps = [{"index": i, "wire_id": f"{i:032x}", "request_digest": "r", "response_digest": "s"} for i in range(9)]
    for fault in runner.CLASSES:
        mutated, _ = runner.mutate(steps, fault, random.Random(7))
        assert mutated == original.mutate(steps, fault, random.Random(7))


# ------------------------------------------------------------------- the arms, through the real agent


def test_arm_s_compaction_fires_and_a_run_of_steps_only_is_clean(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta, fake, run_dir = _run(runner, monkeypatch, tmp_path, "S")
    assert meta["stopped"] == "final" and meta["error"] == ""
    assert meta["n_calls"] == meta["steps"] == meta["step_calls"] == 9
    assert meta["compactions"] >= 2  # structural: the message list shrank, the steplog did not
    assert (meta["summaries"], meta["closes"], meta["switches"], meta["home_extra"]) == (0, 0, 0, [])
    assert all(call["wire_id"] for call in meta["calls"])
    assert _reconcile(run_dir)["clean"]


def test_arm_m_each_summary_call_is_a_wire_record_without_a_step(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta, _, run_dir = _run(runner, monkeypatch, tmp_path, "M")
    assert meta["summaries"] >= 1 and meta["compactions"] >= 1
    audit = _reconcile(run_dir)
    summary_ids = {c["wire_id"] for c in meta["calls"] if c["kind"] == "summary"}
    assert set(audit["missing_steplog"]) == summary_ids  # the predicted false positive, and only it
    assert not audit["missing_wire"] and not audit["altered"]


def test_arm_f_the_primary_is_refused_from_the_fourth_call_and_the_fallback_answers(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta, fake, run_dir = _run(runner, monkeypatch, tmp_path, "F")
    assert meta["stopped"] == "final"
    assert fake.models[:3] == [runner.MODEL] * 3  # the refused attempts never reach the provider
    assert set(fake.models[3:]) == {runner.FALLBACK}
    assert meta["refused_primary"] == meta["n_calls"] - 3 and meta["switches"] == 1
    assert [c["model"] for c in meta["calls"]][3:] == [runner.FALLBACK] * (meta["n_calls"] - 3)
    wire = [json.loads(line) for line in (run_dir / "home" / "wire.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(wire) == meta["n_calls"]  # one record per ANSWERED call, none for a refused attempt
    assert _reconcile(run_dir)["clean"]


def test_arm_t_every_step_streams_and_is_tapped(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta, fake, run_dir = _run(runner, monkeypatch, tmp_path, "T")
    assert meta["stopped"] == "final" and all(fake.streamed)
    assert meta["compactions"] >= 1
    assert _reconcile(run_dir)["clean"]


def test_a_closing_call_at_max_steps_is_a_wire_record_without_a_step(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta, _, run_dir = _run(runner, monkeypatch, tmp_path, "S", max_steps=3)
    assert meta["stopped"] == "max_steps" and meta["closes"] == 1 and meta["steps"] == 3
    close_id = next(c["wire_id"] for c in meta["calls"] if c["kind"] == "close")
    assert _reconcile(run_dir)["missing_steplog"] == [close_id]


def test_the_budget_stops_before_the_call_and_leaves_no_half_run(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import litellm

    fake = FakeProvider(runner.chain(0))
    monkeypatch.setattr(litellm, "completion", fake)
    runner.generate(tmp_path / "out", runner.plan()[:2], budget=3)
    assert len(fake.models) == 3
    assert not any((tmp_path / "out").iterdir())  # the cut run was removed, so a resume starts it again


# -------------------------------------------------------------------------- analysis and the smoke


def test_analysis_attributes_false_positives_and_reads_faults_by_signature(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runs = tmp_path / "runs"
    _run(runner, monkeypatch, runs, "S", name="r00-t00-S")
    _run(runner, monkeypatch, runs, "M", name="r00-t01-M")
    _run(runner, monkeypatch, runs, "S", max_steps=3, name="r01-t00-S")
    (runs / "r01-t01-T" / "home").mkdir(parents=True)
    (runs / "r01-t01-T" / "meta.json").write_text(json.dumps({"error": "boom"}), encoding="utf-8")
    result = runner.analyse(runs, tmp_path / "mutated")
    assert result["protocol_failures"] == {"r01-t01-T": "boom"}
    assert [row["clean_fp"] for row in result["runs"]] == [False, True, True]
    causes = Counter[str]()
    for row in result["runs"]:
        causes.update(row["causes"])
    assert set(causes) == {"wire record without step (summary)", "wire record without step (close)"}
    assert result["signature"] == {"omission": [3, 3], "fabrication": [3, 3], "altered copy": [3, 3]}
    assert result["any_discrepancy"]["clean"] == [2, 3]
    supported, reasons = runner.verdict(result)
    assert not supported and any("false positives" in r for r in reasons) and any("analysable" in r for r in reasons)
    assert any(line.startswith("RULE: no pilot") for line in runner.report_lines(result))


def test_a_dirty_clean_copy_would_have_scored_a_missed_fault_as_found(runner: ModuleType) -> None:
    """Why §6 restates detection: the any-discrepancy reading is true here though the fault is absent."""
    audit = {"missing_steplog": ["close-call"], "missing_wire": [], "altered": [], "duplicate_ids": 0, "clean": False}
    assert not audit["clean"]
    assert not runner.signature_found("omission", audit, "the-deleted-step")
    assert runner.signature_found("omission", {**audit, "missing_steplog": ["the-deleted-step"]}, "the-deleted-step")


def test_the_smoke_refuses_when_compaction_never_fired_or_a_run_failed(runner: ModuleType, tmp_path: Path) -> None:
    def make(name: str, compactions: int, error: str = "") -> None:
        home = tmp_path / name / "home"
        home.mkdir(parents=True, exist_ok=True)
        (home / "wire.jsonl").write_text('{"wire_id": "a"}\n', encoding="utf-8")
        (home / "traces.jsonl").write_text(json.dumps({"steps": [{"wire_id": "a"}]}) + "\n", encoding="utf-8")
        meta = {"compactions": compactions, "switches": 0, "n_calls": 1, "error": error}
        (tmp_path / name / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    make("r00-t02-F", 0)
    assert runner.check_smoke(tmp_path) == (False, "expected 2 smoke runs, found 1")
    make("r00-t03-T", 0)
    assert runner.check_smoke(tmp_path) == (False, "compaction fired in neither smoke run")
    make("r00-t03-T", 2)
    assert runner.check_smoke(tmp_path)[0]
    make("r00-t02-F", 0, error="APIConnectionError: refused")
    ok, why = runner.check_smoke(tmp_path)
    assert not ok and "protocol failure" in why


# ------------------------------------------------------------------ the cause registered, not exercised


def test_the_cache_cannot_serve_an_agent_step_and_a_hit_is_invisible_to_both_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import litellm

    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    calls: list[dict[str, Any]] = []

    def fake(**kwargs: Any) -> Any:
        calls.append(kwargs)
        message = SimpleNamespace(content="same answer", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)

    monkeypatch.setattr(litellm, "completion", fake)
    settings = Settings(_env_file=None, CHIMERA_HOME=str(tmp_path), CHIMERA_WIRE_LOG=True, CHIMERA_CACHE=True)  # type: ignore[call-arg]
    gw = LLMGateway(settings=settings)
    tools = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object", "properties": {}}}}]
    asked = [{"role": "user", "content": "same question"}]
    for _ in range(2):
        gw.complete(asked, model="ollama_chat/x", temperature=0, tools=tools)
    assert len(calls) == 2  # a call with tools is never cached, so an agent step always reaches the tap
    first = gw.complete(asked, model="ollama_chat/x", temperature=0)
    second = gw.complete(asked, model="ollama_chat/x", temperature=0)
    assert len(calls) == 3 and first.wire_id and second.wire_id == ""  # the hit: no call, no record
    rows = (tmp_path / "wire.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 3
