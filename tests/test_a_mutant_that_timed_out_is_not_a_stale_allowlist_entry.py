"""A timeout says the run was slow, not that a test caught the mutant.

The mutation gate fails on a stale allowlist entry — a mutant listed as equivalent that no longer
survives — so the allowlist cannot rot into a blanket exemption. The first version read "no longer
survives" as "status is not survived", and on a loaded machine a few of the slower equivalents end
in ``timeout``: the gate called them stale and failed on noise. The weekly job runs on a 4-vCPU
runner with no re-run step, where that is more likely, not less. Now a timed-out entry is re-run
alone; still timing out, it is a warning; killed on the re-run, it is stale as before.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_GATE = Path(__file__).resolve().parents[1] / "scripts" / "mutation_gate.py"


def _gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mutation_gate", _GATE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["mutation_gate"] = module  # @dataclass resolves its module through sys.modules
    spec.loader.exec_module(module)
    return module


RESULTS = """\
    chimera.example.audit.x_record__mutmut_1: killed
    chimera.example.audit.x_record__mutmut_2: survived
    chimera.example.audit.x_record__mutmut_3: timeout
    chimera.example.audit.x_record__mutmut_4: no tests
    chimera.example.ledger.xǁLedgerǁnote__mutmut_7: caught by type check
"""


def test_every_status_mutmut_prints_is_read_not_only_the_live_ones() -> None:
    statuses = _gate().parse_results(RESULTS)
    assert statuses == {
        "chimera.example.audit.x_record__mutmut_1": "killed",
        "chimera.example.audit.x_record__mutmut_2": "survived",
        "chimera.example.audit.x_record__mutmut_3": "timeout",
        "chimera.example.audit.x_record__mutmut_4": "no tests",
        "chimera.example.ledger.xǁLedgerǁnote__mutmut_7": "caught by type check",
    }


def test_an_allowlisted_mutant_that_still_times_out_alone_is_a_warning_not_a_failure() -> None:
    gate = _gate()
    asked: list[list[str]] = []

    def rerun(names: list[str]) -> dict[str, str]:
        asked.append(names)
        return {n: "timeout" for n in names}

    statuses = {"m.slow": "timeout", "m.eq": "survived"}
    verdict = gate.judge(statuses, ["m.slow", "m.eq"], rerun=rerun)
    assert asked == [["m.slow"]]  # only the timed-out entry is re-run
    assert verdict.stale == []
    assert verdict.inconclusive == ["m.slow"]
    assert not verdict.failed


def test_a_timed_out_entry_that_survives_alone_is_simply_still_true() -> None:
    verdict = _gate().judge(
        {"m.slow": "timeout"}, ["m.slow"], rerun=lambda names: {n: "survived" for n in names}
    )
    assert (verdict.stale, verdict.inconclusive, verdict.failed) == ([], [], False)


def test_a_timed_out_entry_that_is_killed_alone_is_stale() -> None:
    verdict = _gate().judge(
        {"m.slow": "timeout"}, ["m.slow"], rerun=lambda names: {n: "killed" for n in names}
    )
    assert verdict.stale == ["m.slow"]
    assert verdict.failed


def test_the_re_run_s_word_is_taken_only_for_the_mutants_it_re_ran() -> None:
    # Measured on mutmut 3.6: `mutmut run <name>` leaves every other mutant "not checked" on disk.
    # Read back wholesale, the report would count every other survivor as gone ("0 alive").
    def rerun(names: list[str]) -> dict[str, str]:
        return {"m.slow": "survived", "m.eq": "not checked", "m.other": "not checked"}

    verdict = _gate().judge(
        {"m.slow": "timeout", "m.eq": "survived", "m.other": "killed"}, ["m.slow", "m.eq"], rerun
    )
    assert (verdict.stale, verdict.inconclusive, verdict.failed) == ([], [], False)
    assert verdict.statuses == {"m.slow": "survived", "m.eq": "survived", "m.other": "killed"}


def test_a_killed_entry_is_stale_without_a_re_run() -> None:
    def rerun(names: list[str]) -> dict[str, str]:
        raise AssertionError(f"nothing to re-run, asked for {names}")

    verdict = _gate().judge({"m.gone": "killed", "m.eq": "survived"}, ["m.gone", "m.eq"], rerun)
    assert verdict.stale == ["m.gone"]
    assert verdict.failed


def test_a_listed_mutant_missing_from_the_results_is_stale() -> None:
    # Renamed by an edit to the module: the allowlist names a mutant mutmut no longer generates.
    verdict = _gate().judge({}, ["m.renamed"], rerun=lambda names: {})
    assert verdict.stale == ["m.renamed"]


def test_a_survivor_outside_the_allowlist_still_fails_and_a_timeout_outside_it_does_not() -> None:
    verdict = _gate().judge({"m.new": "survived", "m.t": "timeout", "m.n": "no tests"}, [])
    assert verdict.unexpected == ["m.n", "m.new"]
    assert verdict.failed
