"""The label loop — study 22, phase 2.

Every answer a decider gives is written to ``<home>/decisions/decisions.jsonl`` with the number
BEFORE the map; a label is a separate line that says what was true; a refit fits the deployment's own
map on those raw numbers, pooled with the shipped rows while its own labels are thin; a report says
what the log holds. And the one rule the whole loop rests on: **an approval is not a label** — a
person approves a dangerous action they meant to run, so "was it dangerous?" is its own question, on
the card and on the command line.

What each group of tests is against:

* the log keeps ``raw_p`` — a map fitted on the calibrated number it produced learns nothing (§2z);
* the id travels from the band's answer to the verdict, the question file, the card and the record,
  or a label given on the card could not find the number it labels;
* the refit on the bench's own rows reproduces the shipped map (the phase's gate);
* pooling happens only when thin, and only with rows of the same instrument AND build;
* the report says when every label comes from the REVIEW region, where it cannot speak for ALLOW.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from chimera.config import Settings, get_settings
from chimera.decisions import CalibrationMaps, Decider, PlattMap, Reading
from chimera.decisions.governance import DANGER, DECISION
from chimera.decisions.labels import refit, report
from chimera.decisions.local import LocalLogprobBackend
from chimera.decisions.log import DecisionLog, find, log_path, read
from chimera.decisions.maps import SHIPPED_MAPS, SHIPPED_ROWS
from chimera.governance import pending
from chimera.governance.approval import _facts_of, ask_elsewhere
from chimera.governance.band import DecisionBand
from chimera.governance.kernel import TrustKernel

RESOLVED = "qwen3:4b@Q4_K_M"
ACTION = "python -c 'import shutil; shutil.rmtree(\"/home/bruno\")'"
SHIPPED = SHIPPED_MAPS[0]
DIGEST = SHIPPED.prompt_hash


class _Backend:
    """Answers from a script of raw probabilities, under the governance instrument."""

    name = "local_logprob"
    model = "qwen3:4b"

    def __init__(self, raw: list[float], *, build: str = RESOLVED, fail: bool = False) -> None:
        self.raw = list(raw)
        self.build = build
        self.fail = fail
        self._instrument = LocalLogprobBackend("http://127.0.0.1:11434", "qwen3:4b").instrument(DANGER)

    def instrument(self, question: Any) -> str:
        return self._instrument

    def ask(self, state: str, question: Any) -> Reading:
        if self.fail:
            raise ConnectionError("ollama is off")
        nxt = self.raw.pop(0)
        return Reading(choice="BLOCK" if nxt >= 0.5 else "ALLOW", shares=None, p=nxt, mass=0.99,
                       logprobs_came=True, resolved_model=self.build)


def _identity() -> CalibrationMaps:
    return CalibrationMaps([PlattMap(**{**SHIPPED.to_dict(), "id": "identity", "a": 1.0, "b": 0.0})])


# --- the log --------------------------------------------------------------------------------------


def test_every_answer_is_logged_with_the_number_before_the_map(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    answer = Decider(_Backend([0.99]), CalibrationMaps.shipped(), log=log).decide(DECISION, ACTION, DANGER)
    assert answer.log_id and answer.receipt()["log_id"] == answer.log_id
    (row,) = read(log.path)
    assert row.raw_p == pytest.approx(0.99)
    assert row.answer["p"] == pytest.approx(SHIPPED.apply(0.99), abs=1e-4)  # the map was applied…
    assert row.raw_p != pytest.approx(row.answer["p"], abs=1e-3)  # …and the refit reads the other one
    assert row.answer["state"] == ACTION and row.label is None


def test_a_halt_is_logged_as_a_halt_and_carries_no_number(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    answer = Decider(_Backend([], fail=True), log=log).decide(DECISION, ACTION, DANGER)
    (row,) = read(log.path)
    assert answer.halt and row.answer["halt"] and row.raw_p is None


def test_without_a_log_there_is_no_id_and_no_file(tmp_path: Path) -> None:
    answer = Decider(_Backend([0.5])).decide(DECISION, ACTION, DANGER)
    assert answer.log_id == "" and "log_id" not in answer.receipt()


def test_a_later_label_replaces_an_earlier_one_and_an_orphan_labels_nothing(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    entry = log.answer({"decision": DECISION}, "x", raw_p=0.4)
    log.outcome(entry, True, source="card")
    log.outcome(entry, False, source="cli")
    log.outcome("nope", True, source="cli")
    (row,) = read(log.path)
    assert (row.label, row.source) == (0, "cli")
    with pytest.raises(ValueError):
        log.outcome(entry, True, source="guess")


# --- the id travels to the card and the record ----------------------------------------------------


def test_the_id_travels_from_the_answer_to_the_question_and_the_record(tmp_path: Path) -> None:
    log = DecisionLog.for_home(tmp_path)
    band = DecisionBand(Decider(_Backend([0.80]), _identity(), log=log))
    verdict = TrustKernel(band=band).evaluate(ACTION)
    assert verdict.decision_id and find(log.path, verdict.decision_id) is not None
    assert _facts_of(verdict, ACTION)["decision_id"] == verdict.decision_id
    seen: list[Any] = []
    ask = ask_elsewhere(tmp_path, on_asked=seen.append, wait_seconds=0.0)
    assert ask(verdict, ACTION) is False  # silence refuses
    (question,) = seen
    assert question.decision_id == verdict.decision_id
    (line,) = [json.loads(x) for x in (tmp_path / "approvals" / pending.HISTORY).read_text(encoding="utf-8").splitlines()]
    assert line["decision_id"] == verdict.decision_id and line["outcome"] == "timeout"


def test_a_prior_below_the_band_carries_its_id_too(tmp_path: Path) -> None:
    band = DecisionBand(Decider(_Backend([0.10]), _identity(), log=DecisionLog.for_home(tmp_path)))
    verdict = TrustKernel(band=band).evaluate(ACTION)
    assert verdict.band == "allow" and verdict.decision_id


def test_approving_writes_no_label(tmp_path: Path) -> None:
    log = DecisionLog.for_home(tmp_path)
    band = DecisionBand(Decider(_Backend([0.80]), _identity(), log=log))
    verdict = TrustKernel(band=band).evaluate(ACTION)
    directory = tmp_path / "approvals"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "q1.ask.json").write_text(json.dumps({"id": "q1", "action": ACTION}), encoding="utf-8")
    assert pending.answer(tmp_path, "q1", True)
    assert all(r.label is None for r in read(log.path)), "an approval is not a label"
    assert verdict.decision_id


def test_the_label_route_labels_the_answer_and_says_no_for_an_unknown_id(tmp_path: Path) -> None:
    from chimera.api import build_api_app

    home = tmp_path / "home"
    log = DecisionLog.for_home(home)
    entry = log.answer({"decision": DECISION}, ACTION, raw_p=0.9)
    client = TestClient(build_api_app(lambda: None, settings=Settings(CHIMERA_HOME=str(home))))  # type: ignore[arg-type, call-arg]
    assert client.post(f"/api/decisions/{entry}/label", json={"event": True}).json() == {"ok": True}
    assert client.post("/api/decisions/unknown/label", json={"event": True}).json() == {"ok": False}
    (row,) = read(log.path)
    assert (row.label, row.source) == (1, "card")


# --- refit ----------------------------------------------------------------------------------------


def _log_rows(path: Path, pairs: list[tuple[float, int]], *, build: str = RESOLVED, digest: str = DIGEST) -> None:
    log = DecisionLog(path)
    for raw_p, label in pairs:
        receipt = {"decision": DECISION, "backend": "local_logprob", "model": "qwen3:4b", "prompt_hash": digest,
                   "resolved_model": build, "calibrated": True, "p": 0.5}
        log.outcome(log.answer(receipt, "s", raw_p=raw_p), bool(label), source="cli")


def test_the_shipped_rows_are_the_bench_rows_the_map_was_fitted_on() -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bench.jev_decisions.fit_map import DEFAULT_ROWS, calibration_pairs

    assert list(SHIPPED_ROWS[SHIPPED.id]) == calibration_pairs(DEFAULT_ROWS)


def test_a_refit_on_the_bench_rows_reproduces_the_shipped_map(tmp_path: Path) -> None:
    # The phase's gate: the loop, fed the rows the shipped map came from, gives the shipped map.
    _log_rows(tmp_path / "d.jsonl", list(SHIPPED_ROWS[SHIPPED.id]))
    (result,) = refit(read(tmp_path / "d.jsonl"), CalibrationMaps.shipped())
    assert result.map is not None and result.n_pool == 0  # 24 and 31: enough of each, no pooling
    assert result.map.a == pytest.approx(SHIPPED.a, abs=1e-9) and result.map.b == pytest.approx(SHIPPED.b, abs=1e-9)
    assert (result.map.decision, result.map.prompt_hash, result.map.resolved_model) == (DECISION, DIGEST, RESOLVED)


def test_thin_labels_are_pooled_with_the_shipped_rows(tmp_path: Path) -> None:
    _log_rows(tmp_path / "d.jsonl", [(0.99, 1), (0.98, 1), (0.97, 0), (0.95, 1), (0.9, 0), (0.6, 0)])
    (result,) = refit(read(tmp_path / "d.jsonl"), CalibrationMaps.shipped())
    assert result.map is not None and result.n_own == 6 and result.n_pool == 55 and result.map.n == 61
    assert "pooled" in result.reason


def test_thin_labels_of_another_build_are_not_pooled(tmp_path: Path) -> None:
    _log_rows(tmp_path / "d.jsonl", [(0.99, 1), (0.9, 0), (0.8, 1), (0.2, 0)], build="qwen3:4b@F16")
    (result,) = refit(read(tmp_path / "d.jsonl"), CalibrationMaps.shipped())
    assert result.map is None and "no shipped rows" in result.reason


def test_thin_labels_with_no_shipped_instrument_get_no_map(tmp_path: Path) -> None:
    _log_rows(tmp_path / "d.jsonl", [(0.99, 1), (0.9, 0), (0.8, 1), (0.2, 0)], digest="000000000000")
    (result,) = refit(read(tmp_path / "d.jsonl"), CalibrationMaps.shipped())
    assert result.map is None


def test_the_refit_reads_raw_p_not_the_calibrated_p(tmp_path: Path) -> None:
    # Every logged `p` is 0.5; a refit that read it would see one value and could not separate labels.
    _log_rows(tmp_path / "d.jsonl", [(0.9, 1)] * 20 + [(0.1, 0)] * 20)
    (result,) = refit(read(tmp_path / "d.jsonl"), CalibrationMaps.shipped())
    assert result.map is not None and result.map.apply(0.9) > 0.9 and result.map.apply(0.1) < 0.1


# --- report ---------------------------------------------------------------------------------------


def test_the_report_counts_the_review_budget_and_where_the_labels_came_from(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    base = {"decision": DECISION, "backend": "local_logprob", "model": "qwen3:4b", "prompt_hash": DIGEST,
            "resolved_model": RESOLVED, "calibrated": True}
    ids = [log.answer({**base, "p": p}, "s", raw_p=p) for p in (0.9, 0.8, 0.4, 0.1)]
    log.answer({**base, "calibrated": False, "halt": "off"}, "s", raw_p=None)
    log.outcome(ids[0], True, source="card")
    log.outcome(ids[1], False, source="card")
    (g,) = report(read(log.path), review_at=0.5, allow_below=0.3)
    assert g.answers == 5 and g.halts == 1
    assert g.regions == {"review": 2, "uncertain": 1, "allow": 1, "no_p": 1}
    assert g.review_per_100 == pytest.approx(50.0)
    assert g.labelled_by_region["review"] == 2 and g.labelled == 2
    assert g.catch == (1, 1) and g.false_refusal == (1, 1)
    assert g.brier is not None and not math.isnan(g.brier)


# --- the command line -----------------------------------------------------------------------------


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    yield home
    get_settings.cache_clear()


def test_the_cli_labels_and_refits_only_with_write(cli_home: Path) -> None:
    from chimera.cli.main import app

    _log_rows(log_path(cli_home), list(SHIPPED_ROWS[SHIPPED.id]))
    entry = DecisionLog.for_home(cli_home).answer({"decision": DECISION}, ACTION, raw_p=0.7)
    runner = CliRunner()
    assert runner.invoke(app, ["decisions", "label", entry]).exit_code == 1  # which one must be said
    assert runner.invoke(app, ["decisions", "label", entry, "--no"]).exit_code == 0
    assert find(log_path(cli_home), entry).label == 0  # type: ignore[union-attr]
    assert runner.invoke(app, ["decisions", "label", "missing", "--yes"]).exit_code == 1
    maps = cli_home / "decisions" / "maps.json"
    dry = runner.invoke(app, ["decisions", "refit"])
    assert dry.exit_code == 0 and not maps.exists()
    assert runner.invoke(app, ["decisions", "refit", "--write"]).exit_code == 0
    saved = CalibrationMaps.load(maps).find(DECISION, "local_logprob", "qwen3:4b", DIGEST)
    assert saved is not None and saved.n == 55
    out = runner.invoke(app, ["decisions", "report"])
    assert out.exit_code == 0 and DECISION in out.output
