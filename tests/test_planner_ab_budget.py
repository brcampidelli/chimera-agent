"""Offline planner A/B regressions for the section 6 budget and credential boundary."""

from __future__ import annotations

import json
import os
import random
import shlex
import shutil
import subprocess
import sys
from itertools import product
from pathlib import Path

import pytest

from bench.planner_ab import run_ab


def _log(path: Path, *rows: tuple[str, str, str, float | None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({
        "tag": tag, "arm": arm, "task": task, "hid": run_ab.hid_of(tag, arm, 0),
        "real_usd": usd, "receiptless": usd is None,
    }) + "\n" for tag, arm, task, usd in rows), encoding="utf-8")


def _receipt(homes: Path, tag: str, arm: str, task: str, tokens: int) -> str:
    name = f"{task}-{run_ab.hid_of(tag, arm, 0)}"
    path = homes / name / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"attempts": [{"prompt_tokens": tokens}]}) + "\n", encoding="utf-8")
    return name


def test_global_spend_estimates_missing_receipts_per_model_and_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    homes = tmp_path / "homes"
    log = tmp_path / "driver.jsonl"
    discarded = tmp_path / "discarded"
    monkeypatch.setattr(run_ab, "HOMES", homes)
    monkeypatch.setattr(run_ab, "DRIVER_LOG", log)
    monkeypatch.setattr(run_ab, "DISCARDED_PILOT", discarded)
    monkeypatch.setattr(run_ab, "HB", tmp_path / "harness")
    result = run_ab.results_root() / run_ab.hid_of("glm", "noplan", 0) / "session" / "orphan.json"
    result.parent.mkdir(parents=True)
    result.write_text("{}", encoding="utf-8")
    _log(log, ("glm", "plan", "one", 0.10), ("glm", "plan", "one", 0.10),
         ("glm", "plan", "two", None), ("glm", "noplan", "three", 0.02),
         ("qwen", "plan", "one", 0.20), ("qwen", "plan", "two", None))
    _log(discarded / log.name, ("glm", "plan", "one", 0.04))
    same_result = result.parents[2] / run_ab.hid_of("glm", "plan", 0) / "session" / "one.json"
    same_result.parent.mkdir(parents=True)
    same_result.write_text("{}", encoding="utf-8")
    _receipt(homes, "glm", "plan", "one", 1_000_000)  # latest completed attempt is already in the log
    pending = _receipt(homes, "qwen", "plan", "new", 1_000_000)
    expected = (0.10 * 3 + 0.02 * 2 + 0.20 * 2 + 0.04 + run_ab.PRICE["qwen"][0]) * run_ab.SAFETY
    assert run_ab.total_spent({pending}) == pytest.approx(expected)


def test_pilot_projection_counts_missing_receipts_without_blending_arms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = tmp_path / "driver.jsonl"
    monkeypatch.setattr(run_ab, "DRIVER_LOG", log)
    monkeypatch.setattr(run_ab, "HOMES", tmp_path / "homes")
    pilot = [("plan", "one", 0), ("plan", "two", 0), ("plan", "three", 0), ("noplan", "four", 0)]
    for arm, task, tokens in (("plan", "one", 1000), ("noplan", "four", 5000)):
        _receipt(run_ab.HOMES, "qwen", arm, task, tokens)
    _log(log, ("qwen", "plan", "two", None), ("qwen", "plan", "three", None))
    monkeypatch.setattr(run_ab, "result_path", lambda hid, task: "result.json" if task in {"one", "four"} else None)
    monkeypatch.setattr(run_ab, "PILOT_N", len(pilot))
    expected_tokens = (1000 * 3 + 5000) / len(pilot)
    assert run_ab.pilot_mean("qwen", pilot) == pytest.approx(expected_tokens * run_ab.PRICE["qwen"][0] / 1e6)
    with pytest.raises(SystemExit, match="pilot is incomplete"):
        run_ab.pilot_mean("qwen", [*pilot, ("plan", "not-run", 0)])


@pytest.fixture
def e3_design(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[run_ab.Triple]:
    monkeypatch.setattr(run_ab, "TASKS", ["one", "two", "three"])
    monkeypatch.setattr(run_ab, "PILOT_N", 12)
    monkeypatch.setattr(run_ab, "HOMES", tmp_path / "homes")
    monkeypatch.setattr(run_ab, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(run_ab, "HB", tmp_path / "harness")
    monkeypatch.setattr(run_ab, "DRIVER_LOG", tmp_path / "driver.jsonl")
    monkeypatch.setattr(run_ab, "DISCARDED_PILOT", tmp_path / "discarded")
    monkeypatch.setattr(run_ab, "WRAPPER", str(Path(run_ab.__file__).with_name("p101_solve.sh")))
    monkeypatch.setattr(run_ab, "write_config", lambda _tag: None)
    monkeypatch.setattr(sys, "argv", ["run_ab.py", "--tag", "qwen", "--phase", "rest", "--model-cap", "0.01"])
    for name in run_ab.LEGACY_VALUES:
        monkeypatch.setenv(name, "0")
    run_ab.HOMES.mkdir()
    run_ab.LOGS.mkdir()
    design = list(product(run_ab.ARMS, run_ab.TASKS, run_ab.REPLICAS))
    random.Random(run_ab.SEED).shuffle(design)
    return design


def _runs(tag: str, design: list[run_ab.Triple]) -> set[tuple[str, str]]:
    return {(run_ab.hid_of(tag, arm, replica), task) for arm, task, replica in design}


def _record_solve(submitted: set[tuple[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    def solve(hid: str, task: str, tag: str, arm: str, replica: int, _driver_fd: int | None) -> dict:
        submitted.add((hid, task))
        return {"tag": tag, "task": task, "hid": hid, "arm": arm, "replica": replica, "rc": 0,
                "receiptless": False, "outcome": 1.0, "real_usd": 0.01, "seconds": 1.0}

    monkeypatch.setattr(run_ab, "run_one", solve)


@pytest.mark.parametrize(("spent", "queued"), [(2.25, True), (2.26, False)])
def test_qwen_schedules_the_full_pending_design_or_drops_it(
    monkeypatch: pytest.MonkeyPatch, e3_design: list[run_ab.Triple], spent: float, queued: bool,
) -> None:
    pilot = _runs("qwen", e3_design[:run_ab.PILOT_N])
    existing = _runs("glm", e3_design) | pilot
    monkeypatch.setattr(run_ab, "pilot_mean", lambda _tag, _pilot: 2.25 / len(e3_design) / run_ab.SAFETY)
    monkeypatch.setattr(run_ab, "total_spent", lambda _in_flight: spent)
    monkeypatch.setattr(run_ab, "result_path", lambda hid, task: "existing.json" if (hid, task) in existing else None)
    submitted: set[tuple[str, str]] = set()
    _record_solve(submitted, monkeypatch)
    run_ab.main()

    scheduled = _runs("qwen", e3_design) - pilot
    assert submitted == (scheduled if queued else set())
    decision = json.loads((run_ab.HOMES / ".e3-admission.json").read_text(encoding="utf-8"))
    assert decision["admitted"] is queued


@pytest.mark.parametrize("spent_after_resume", [3.0, 4.0])
def test_qwen_reuses_admission_after_progress_and_enforces_the_hard_stop(
    monkeypatch: pytest.MonkeyPatch, e3_design: list[run_ab.Triple], spent_after_resume: float,
) -> None:
    existing = _runs("glm", e3_design) | _runs("qwen", e3_design[:run_ab.PILOT_N])
    monkeypatch.setattr(run_ab, "result_path", lambda hid, task: "existing.json" if (hid, task) in existing else None)
    monkeypatch.setattr(run_ab, "pilot_mean", lambda _tag, _pilot: 2.25 / len(e3_design) / run_ab.SAFETY)
    assert run_ab._e3_admitted(e3_design, 2.25)

    existing |= _runs("qwen", e3_design[run_ab.PILOT_N:run_ab.PILOT_N + 1])
    monkeypatch.setattr(run_ab, "pilot_mean", lambda *_args: pytest.fail("admission must not be recomputed"))
    monkeypatch.setattr(run_ab, "total_spent", lambda _in_flight: spent_after_resume)
    submitted: set[tuple[str, str]] = set()
    _record_solve(submitted, monkeypatch)
    run_ab.main()
    if spent_after_resume < run_ab.HARD_STOP_USD:
        assert submitted == _runs("qwen", e3_design) - existing
    else:
        assert not submitted


def test_e3_admission_requires_the_complete_admitted_e2_design_and_matches_its_manifest(
    monkeypatch: pytest.MonkeyPatch, e3_design: list[run_ab.Triple],
) -> None:
    task_count = 1
    e2_design = run_ab._e2_queue(e3_design, task_count)
    complete = _runs("glm", e2_design + e3_design[:run_ab.PILOT_N])
    existing = complete - {next(iter(complete))}
    e2_path = run_ab.HOMES / ".e2-design.json"
    run_ab._save_state(e2_path, {
        "manifest": run_ab._manifest("glm", e3_design), "model_cap": 2.50, "task_count": task_count,
    })
    monkeypatch.setattr(run_ab, "result_path", lambda hid, task: "existing.json" if (hid, task) in existing else None)
    monkeypatch.setattr(run_ab, "pilot_mean", lambda _tag, _pilot: 0.01)
    with pytest.raises(SystemExit, match="finish E2"):
        run_ab._e3_admitted(e3_design, 2.25)
    assert not (run_ab.HOMES / ".e3-admission.json").exists()

    existing |= complete
    assert complete < _runs("glm", e3_design)
    assert run_ab._e3_admitted(e3_design, 2.25)

    monkeypatch.setitem(run_ab.MODELS, "qwen", "different-route")
    with pytest.raises(SystemExit, match="E3 admission checkpoint does not match"):
        run_ab._e3_admitted(e3_design, 2.25)


def test_qwen_fails_closed_if_a_prior_remainder_run_has_no_admission(
    monkeypatch: pytest.MonkeyPatch, e3_design: list[run_ab.Triple],
) -> None:
    existing = _runs("glm", e3_design) | _runs("qwen", e3_design[:run_ab.PILOT_N + 1])
    monkeypatch.setattr(run_ab, "result_path", lambda hid, task: "existing.json" if (hid, task) in existing else None)
    monkeypatch.setattr(run_ab, "pilot_mean", lambda *_args: pytest.fail("cannot establish original admission"))
    with pytest.raises(SystemExit, match="without a saved admission"):
        run_ab._e3_admitted(e3_design, 2.25)


def test_retry_archives_and_recounts_the_previous_receipt_exactly_once(e3_design: list[run_ab.Triple]) -> None:
    name = f"one-{run_ab.hid_of('glm', 'plan', 0)}"
    unit = run_ab.PRICE["glm"][0] * run_ab.SAFETY
    run_ab._prepare_attempt(name)
    _receipt(run_ab.HOMES, "glm", "plan", "one", 1_000_000)
    original_receipt = (run_ab.HOMES / name / "runs.jsonl").read_bytes()
    assert run_ab.total_spent(set()) == pytest.approx(unit)

    run_ab._prepare_attempt(name)
    archive = list((run_ab.HOMES / ".interrupted").glob(f"*/{name}/runs.jsonl"))
    assert len(archive) == 1 and archive[0].read_bytes() == original_receipt
    assert run_ab.total_spent({name}) == pytest.approx(unit)

    _receipt(run_ab.HOMES, "glm", "plan", "one", 2_000_000)
    assert run_ab.total_spent({name}) == pytest.approx(3 * unit)
    _log(run_ab.DRIVER_LOG, ("glm", "plan", "one", 2 * run_ab.PRICE["glm"][0]))
    row = json.loads(run_ab.DRIVER_LOG.read_text(encoding="utf-8"))
    row["attempt_id"] = run_ab._attempt_id(run_ab.HOMES / name)
    run_ab.DRIVER_LOG.write_text(json.dumps(row) + "\n", encoding="utf-8")

    assert run_ab.total_spent(set()) == pytest.approx(3 * unit)
    run_ab._prepare_attempt(name)
    assert run_ab.total_spent({name}) == pytest.approx(3 * unit)


def test_retry_estimates_unlogged_receiptless_spend_at_its_arm_mean(e3_design: list[run_ab.Triple]) -> None:
    name = f"one-{run_ab.hid_of('glm', 'plan', 0)}"
    _log(run_ab.DRIVER_LOG, ("glm", "plan", "two", 0.12))
    run_ab._prepare_attempt(name)
    assert run_ab.total_spent(set()) == pytest.approx(0.24 * run_ab.SAFETY)
    run_ab._prepare_attempt(name)
    assert run_ab.total_spent({name}) == pytest.approx(0.24 * run_ab.SAFETY)


def test_retry_refuses_ambiguous_legacy_spend(e3_design: list[run_ab.Triple]) -> None:
    _log(run_ab.DRIVER_LOG, ("glm", "plan", "one", 0.12))
    name = _receipt(run_ab.HOMES, "glm", "plan", "one", 1_000_000)
    with pytest.raises(SystemExit, match="cannot reconcile legacy spend"):
        run_ab.total_spent(set())
    assert (run_ab.HOMES / name / "runs.jsonl").exists()


def _outcome(tag: str, triple: run_ab.Triple, *, rc: int = 0, receiptless: bool = False) -> dict:
    arm, task, replica = triple
    return {"tag": tag, "task": task, "hid": run_ab.hid_of(tag, arm, replica), "arm": arm,
            "replica": replica, "rc": rc, "receiptless": receiptless, "outcome": 1.0,
            "real_usd": None if receiptless else 0.01, "seconds": 1.0, "stderr_tail": ""}


def test_apparatus_counts_pilot_and_rest_and_stays_stopped_after_later_successes(
    e3_design: list[run_ab.Triple], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(run_ab, "TASKS", ["one", "two", "three", "four", "five"])
    design = list(product(run_ab.ARMS, run_ab.TASKS, run_ab.REPLICAS))
    random.Random(run_ab.SEED).shuffle(design)
    pilot = design[:run_ab.PILOT_N]
    pilot_rows = [_outcome("glm", triple, rc=int(i == 0), receiptless=(i == 1))
                  for i, triple in enumerate(pilot)]
    # Other executors and the discarded pilot have no vote in GLM's apparatus gate.
    qwen = [_outcome("qwen", triple, rc=1) for triple in design]
    discarded = run_ab.DISCARDED_PILOT / run_ab.DRIVER_LOG.name
    discarded.parent.mkdir()
    discarded.write_text(
        "".join(json.dumps(_outcome("glm", triple, rc=1)) + "\n" for triple in pilot), encoding="utf-8",
    )
    run_ab.DRIVER_LOG.write_text(
        "".join(json.dumps(row) + "\n" for row in qwen + pilot_rows), encoding="utf-8",
    )
    present = _runs("glm", pilot)
    monkeypatch.setattr(run_ab, "result_path", lambda hid, task: "existing.json" if (hid, task) in present else None)
    monkeypatch.setattr(run_ab, "pilot_mean", lambda _tag, _pilot: 0.001)
    monkeypatch.setattr(run_ab, "total_spent", lambda _in_flight: 0.50)
    monkeypatch.setattr(sys, "argv", ["run_ab.py", "--tag", "glm", "--phase", "rest", "--concurrency", "1"])
    submitted: list[tuple[str, str]] = []

    def solve(hid: str, task: str, tag: str, arm: str, replica: int, _fd: int | None) -> dict:
        submitted.append((hid, task))
        return _outcome(tag, (arm, task, replica))

    monkeypatch.setattr(run_ab, "run_one", solve)
    run_ab.main()

    assert submitted == [(run_ab.hid_of("glm", arm, replica), task)
                         for arm, task, replica in design[12:20]]
    assert "HALT: 2/20" in capsys.readouterr().out

    # Older drivers could resume after a stop and dilute the rate with more successes.
    # Replaying the prefix must keep the stop even after the final rate reaches 5%.
    with run_ab.DRIVER_LOG.open("a", encoding="utf-8") as log:
        for triple in (design[20:] + design[:10]):
            log.write(json.dumps(_outcome("glm", triple)) + "\n")
    monkeypatch.setattr(run_ab, "write_config", lambda _tag: pytest.fail("the stopped executor was admitted"))
    run_ab.main()
    assert len(submitted) == 8
    assert "already crossed the stop at 2/20 (now 2/40)" in capsys.readouterr().out


def test_apparatus_reconciles_unlogged_outcomes_and_deduplicates_by_attempt_id(
    e3_design: list[run_ab.Triple],
) -> None:
    name = f"one-{run_ab.hid_of('glm', 'plan', 0)}"
    run_ab._prepare_attempt(name)
    _receipt(run_ab.HOMES, "glm", "plan", "one", 1_000_000)
    unlogged_result = run_ab.results_root() / run_ab.hid_of("glm", "plan", 0) / "session" / "two.json"
    unlogged_result.parent.mkdir(parents=True)
    unlogged_result.write_text("{}", encoding="utf-8")

    rows = run_ab._recover_apparatus("glm")
    assert len(rows) == 2
    logged_claim, orphan_result = rows
    assert logged_claim["attempt_id"] == run_ab._attempt_id(run_ab.HOMES / name)
    assert logged_claim["rc"] is None and not logged_claim["receiptless"]
    assert orphan_result["rc"] is None and orphan_result["receiptless"]
    assert run_ab._apparatus_progress(rows, "glm") == (2, 2, None)
    spent = run_ab.total_spent(set())

    with run_ab.DRIVER_LOG.open("a", encoding="utf-8") as log:
        log.write(json.dumps(logged_claim) + "\n")
    assert run_ab._recover_apparatus("glm") == rows
    assert run_ab.total_spent(set()) == spent


@pytest.mark.parametrize("concurrency", [-1, 0, 5])
def test_concurrency_outside_the_preregistered_limit_is_rejected_before_start(
    e3_design: list[run_ab.Triple], monkeypatch: pytest.MonkeyPatch, concurrency: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["run_ab.py", "--tag", "glm", "--phase", "pilot",
                                     "--concurrency", str(concurrency)])
    monkeypatch.setattr(run_ab, "_run", lambda *_args: pytest.fail("invalid driver invocation"))
    with pytest.raises(SystemExit) as error:
        run_ab.main()
    assert error.value.code == 2
    assert "choose from 1, 2, 3, 4" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None, reason="Bash launcher")
@pytest.mark.parametrize("inherit", [False, True])
def test_launcher_exports_only_the_required_key(
    tmp_path: Path, inherit: bool,
) -> None:
    launcher = tmp_path / "p101_launch.sh"
    source = Path(run_ab.__file__).with_name(launcher.name).read_text(encoding="utf-8")
    assignment = next(line for line in source.splitlines() if line.startswith("REPO="))
    launcher.write_text(source.replace(assignment, f"REPO={shlex.quote(str(tmp_path))}"), encoding="utf-8")
    (tmp_path / "p101_solve.sh").touch()
    (tmp_path / ".env").write_bytes(b"OTHER_KEY=file_value\r\nOPENROUTER_API_KEY='file_value'\r\n")
    fake_python = tmp_path / "hb-venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text(
        '#!/usr/bin/env bash\n'
        '[[ "$OPENROUTER_API_KEY" == "$3" && ! -v OTHER_KEY && ! -v INHERITED_OTHER ]]\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o700)
    env = {"HOME": str(tmp_path), "PATH": os.environ["PATH"], "INHERITED_OTHER": "ignored"}
    expected_key = "file_value"
    if inherit:
        env["OPENROUTER_API_KEY"] = expected_key = "inherited_value"
    result = subprocess.run(["bash", "-x", str(launcher), expected_key], env=env,
                            check=False, capture_output=True, text=True)
    assert result.returncode == 0
    assert expected_key not in result.stdout + result.stderr
