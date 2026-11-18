"""Offline guards for the P1-02 reader and paid runner."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_BENCH = Path(__file__).resolve().parents[1] / "bench" / "memory_extraction"


@pytest.fixture
def runner() -> Iterator[ModuleType]:
    # The frozen corpus imports its sibling by short name. Keep those aliases local to this
    # fixture so running a different bench before or after it can't change which corpus loads.
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(_BENCH))
        for name in ("items", "items_p1_02"):
            patch.setitem(sys.modules, name, None)
            del sys.modules[name]
        spec = importlib.util.spec_from_file_location("p102_runner", _BENCH / "run.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module


def _ledger(runner: ModuleType, path: Path, **spend: float) -> None:
    arms = {**dict.fromkeys(runner.ARMS, 0.0), **spend}
    path.write_text(json.dumps({"version": 2, "usd": sum(arms.values()), "arms": arms}), encoding="utf-8")


def _gateway(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    worst_case: float | None,
    complete: object,
) -> None:
    monkeypatch.setattr(runner, "worst_case_usd", lambda _gateway, _messages, _kwargs, *, strict: worst_case)
    monkeypatch.setattr("chimera.providers.LLMGateway", lambda _settings: SimpleNamespace(complete=complete))


@pytest.mark.parametrize(
    ("prior", "reason"),
    [
        ({"ctrl": 0.20}, "ctrl"),
        ({"base": 0.70, "base_cap": 0.75}, "task ledger"),
    ],
)
def test_cost_must_fit_both_registered_ceilings_before_the_gateway_is_called(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prior: dict[str, float], reason: str,
) -> None:
    path = tmp_path / "ledger.json"
    _ledger(runner, path, **prior)
    _gateway(monkeypatch, runner, 0.06, lambda *_a, **_k: pytest.fail("unaffordable call reached gateway"))
    backend = runner._Pinned(runner.ARMS["ctrl"], path)

    with pytest.raises(runner._StopRun, match=reason):
        backend.complete([])
    assert runner._load_ledger(path) == {**dict.fromkeys(runner.ARMS, 0.0), **prior}


def test_reservation_is_persisted_before_send_and_shared_by_parallel_workers(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "ledger.json"
    _ledger(runner, path, ctrl=0.20)
    entered = threading.Event()
    finish = threading.Event()

    def complete(*_args: object, **_kwargs: object) -> SimpleNamespace:
        assert runner._load_ledger(path)["ctrl"] == pytest.approx(0.24)
        entered.set()
        assert finish.wait(5)
        return SimpleNamespace(usd=0.02)

    _gateway(monkeypatch, runner, 0.04, complete)
    backend = runner._Pinned(runner.ARMS["ctrl"], path)
    with ThreadPoolExecutor() as pool:
        first = pool.submit(backend.complete, [])
        try:
            assert entered.wait(5)
            with pytest.raises(runner._StopRun, match="ctrl"):
                backend.complete([])
        finally:
            finish.set()
        first.result()
    assert runner._load_ledger(path)["ctrl"] == pytest.approx(0.22)


def test_provider_error_keeps_reservation_on_disk_across_restarts(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "ledger.json"

    def provider_error(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError("provider did not confirm its bill")

    _gateway(monkeypatch, runner, 0.20, provider_error)
    with pytest.raises(TimeoutError):
        runner._Pinned(runner.ARMS["ctrl"], path).complete([])
    assert runner._load_ledger(path)["ctrl"] == pytest.approx(0.20)

    _gateway(monkeypatch, runner, 0.06, lambda *_a, **_k: pytest.fail("uncertain spend was forgotten"))
    with pytest.raises(runner._StopRun, match="ctrl"):
        runner._Pinned(runner.ARMS["ctrl"], path).complete([])


def test_the_provider_error_stop_prevents_the_next_queued_call(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reached = 0

    def complete(*_args: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal reached
        reached += 1
        if reached <= 2:
            raise TimeoutError
        return SimpleNamespace(usd=0.0001)

    _gateway(monkeypatch, runner, 0.001, complete)
    backend = runner._Pinned(runner.ARMS["ctrl"], tmp_path / "ledger.json")
    for _ in range(2):
        with pytest.raises(TimeoutError):
            backend.complete([])
    for _ in range(8):
        backend.complete([])
    with pytest.raises(runner._StopRun, match="provider errors 2/10"):
        backend.complete([])
    assert reached == 10


def test_unknown_bound_cannot_reach_a_provider(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "ledger.json"
    _gateway(monkeypatch, runner, None, lambda *_a, **_k: pytest.fail("unknown quote was admitted"))
    with pytest.raises(runner._StopRun, match="cannot bound"):
        runner._Pinned(runner.ARMS["ctrl"], path).complete([])
    assert not path.exists()


def _history(runner: ModuleType, root: Path) -> tuple[Path, dict[Path, bytes]]:
    folder = root / "results" / "p1-02"
    folder.mkdir(parents=True)
    for name in [*runner.ARMS, "ledger"]:
        target = folder / f"{name}.json"
        target.write_bytes((_BENCH / "results" / "p1-02" / target.name).read_bytes())
    return folder / "ledger.json", {path: path.read_bytes() for path in folder.glob("*.json")}


def test_historical_total_recovers_ctrl_and_refuses_an_unexplained_gap(
    runner: ModuleType, tmp_path: Path,
) -> None:
    historical, original = _history(runner, tmp_path)
    operational = historical.parent / "runtime" / "ledger.json"
    spend = runner._load_ledger(operational, historical)
    assert spend["ctrl"] == pytest.approx(0.021019)
    assert sum(spend.values()) == pytest.approx(0.055538)
    assert not operational.exists()
    assert all(path.read_bytes() == data for path, data in original.items())
    historical.write_text('{"usd": 0.12}', encoding="utf-8")
    with pytest.raises(SystemExit, match="does not reconcile"):
        runner._load_ledger(operational, historical)


def test_new_out_preserves_history_and_reconciles_uncertain_spend_on_restart(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    historical, original = _history(runner, tmp_path)
    operational = historical.parent / "runtime" / "ledger.json"
    monkeypatch.setattr(runner, "HERE", tmp_path)
    arm = runner._Arm("ctrl", runner.MODEL, runner.PROVIDER, 6000, False, (runner.ITEMS[0],))

    def lost_response(*_a: object, **_k: object) -> None:
        assert json.loads(operational.read_text(encoding="utf-8"))["usd"] == pytest.approx(0.075538)
        raise TimeoutError

    _gateway(monkeypatch, runner, 0.02, lost_response)
    runner.run(tmp_path / "first.json", 1, 1, arm)
    first = json.loads((tmp_path / "first.json").read_text(encoding="utf-8"))
    assert first["estimated_usd"] == pytest.approx(0.02)
    assert json.loads(operational.read_text(encoding="utf-8"))["usd"] == pytest.approx(0.075538)

    def recorded_response(*_a: object, **_k: object) -> SimpleNamespace:
        assert json.loads(operational.read_text(encoding="utf-8"))["usd"] == pytest.approx(0.095538)
        return SimpleNamespace(content='{"operations": []}', usd=0.003, provider="fake")

    _gateway(monkeypatch, runner, 0.02, recorded_response)
    runner.run(tmp_path / "second.json", 1, 1, arm)
    assert json.loads(operational.read_text(encoding="utf-8"))["usd"] == pytest.approx(0.078538)
    assert all(path.read_bytes() == data for path, data in original.items())


@pytest.mark.parametrize("suffix", ["ledger.json", "runtime/ledger.json"])
def test_neither_ledger_can_be_the_output_even_before_operational_state_exists(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str,
) -> None:
    historical, original = _history(runner, tmp_path)
    monkeypatch.setattr(runner, "HERE", tmp_path)
    monkeypatch.setattr(runner, "_Pinned", lambda *_a: pytest.fail("ledger output was admitted"))
    with pytest.raises(SystemExit, match="historical or operational spend ledger"):
        runner.run(historical.parent / suffix, 2, 8)
    assert not (historical.parent / "runtime").exists()
    assert all(path.read_bytes() == data for path, data in original.items())


@pytest.mark.parametrize("arm_name", ["ctrl", "base", "base_cap", "weak", "weak_pf"])
def test_every_arm_respects_explicit_out(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arm_name: str,
) -> None:
    out = tmp_path / "new.json"
    observed: list[tuple[Path, str]] = []
    monkeypatch.setattr(sys, "argv", ["run.py", "--run", "--arm", arm_name, f"--out={out}"])
    monkeypatch.setattr(runner, "run", lambda path, _copies, _workers, arm: observed.append((path, arm.name)))
    runner.main()
    assert observed == [(out, arm_name)]


def test_existing_results_are_preserved_before_building_a_gateway(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "historical.json"
    out.write_text("record from an earlier run", encoding="utf-8")
    monkeypatch.setattr(runner, "_Pinned", lambda *_a: pytest.fail("existing output was admitted"))
    with pytest.raises(SystemExit, match="result already exists"):
        runner.run(out, 2, 8, runner.ARMS["base"])
    assert out.read_text(encoding="utf-8") == "record from an earlier run"


def test_parse_error_keeps_its_call_and_charge(runner: ModuleType) -> None:
    def invalid(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(content="{", usd=0.012, prompt_tokens=900, completion_tokens=800, provider="fake")

    backend = SimpleNamespace(complete=invalid)
    row = runner._turn(backend, runner.LONG[6], runner.ARMS["weak"])
    assert row["error"].startswith("parse")
    assert row["called"] is True and row["call_usd"] == 0.012
    assert row["tokens"] == [900, 800]


def test_r_route_requires_completed_decision_costs_and_excludes_the_held_out(
    runner: ModuleType,
) -> None:
    decision = [
        {"id": item.id, "runs": [{"called": True, "call_usd": 0.001} for _ in range(2)]}
        for item in (*runner.ITEMS, *runner.LONG)
    ]
    held = [
        {"id": item.id, "runs": [{"called": True, "call_usd": 9.99} for _ in range(2)]}
        for item in runner.HELD
    ]
    payload = {"usd": 0.000_000_1, "rows": decision + held}
    assert runner._decision_cost(payload) == pytest.approx(0.10)
    decision[-1]["runs"][0]["call_usd"] = None
    assert runner._decision_cost(payload) is None


def test_a_failed_parse_keeps_the_expected_fact_slots_on_the_decision_set(runner: ModuleType) -> None:
    items = (*runner.ITEMS, *runner.LONG)
    rows = [{
        "id": item.id,
        "runs": [{"error": None, "saved": [" ".join(alts[0]) for alts in item.expected]} for _ in range(2)],
    } for item in items]
    item, row = next((item, row) for item, row in zip(items, rows, strict=True) if item.expected)
    row["runs"][1] = {"error": "parse: ValueError"}

    summary = runner.summarise(rows, "saved")
    assert sum(len(row["runs"]) for row in rows) == 120
    assert summary["expected"] == 48
    assert summary["recalled"] == 48 - len(item.expected)
    assert summary["recall"] == (48 - len(item.expected)) / 48


def test_historical_control_and_call_counts_are_read_without_changing_the_records(
    runner: ModuleType, capsys: pytest.CaptureFixture[str],
) -> None:
    runner.compare(_BENCH / "results" / "p1-02")
    stdout = capsys.readouterr().out
    assert "CONTROL FAILED: 30/30 correct vs required 31/31, poison 0, recall 32/36" in stdout
    assert "Model calls over all recorded arms: 650" in stdout
    weak = stdout.split("[weak]", 1)[1].split("[weak_pf]", 1)[0]
    assert "model calls 148/148" in weak and "errors 2" in weak
    assert "recall 36/48" in weak
    assert "R-route cost on the 120 decision turns: UNVERIFIED" in stdout
    assert "DECISION: R-cap and R-route not adopted." in stdout
