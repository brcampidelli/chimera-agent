"""Tests for the memory-graph bench.

The thing worth testing here is not that the statistics work — `tests/test_paired.py` covers that.
It is the **wiring**: that the bench really runs production recall rather than a lookalike, that the
arms differ only by the graph, that the guards are in the execution path instead of merely defined,
and that "how much the graph acted" counts triples that reached the prompt rather than candidates
that did not. Every guard below is exercised by feeding it the broken state it exists to catch — a
guard that has never fired may be inert.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from chimera.eval import memory_graph_bench as bench
from chimera.eval.memory_graph_bench import (
    MULTIHOP,
    SINGLE,
    TRAVERSE,
    GraphBenchError,
    GraphBenchReport,
    GraphCorpus,
    GraphTask,
    SliceReport,
    TaskResult,
    build_corpus,
    build_recall_graph,
    paired_delta_se,
    run_seed,
)
from chimera.interface import session as session_module
from chimera.memory.graph import build_graph
from chimera.memory.manager import MemoryManager
from chimera.memory.store import MemoryStore

REPO_ROOT = Path(__file__).resolve().parent.parent


def _manager(tmp_path: Path, facts: list[str]) -> MemoryManager:
    manager = MemoryManager(MemoryStore(tmp_path / "memory.json"))
    for fact in facts:
        manager.add(fact)
    return manager


def _task(slice_name: str = MULTIHOP, needed: tuple[str, ...] = ("Orion uses the queue.",)) -> GraphTask:
    return GraphTask("t1", slice_name, "Orion", "Give me the full picture on Orion.", needed)


def _result(**kwargs: Any) -> TaskResult:
    base: dict[str, Any] = {
        "seed": 42,
        "task_id": "t",
        "slice": MULTIHOP,
        "entity": "Orion",
        "baseline_pass": False,
        "graph_pass": True,
        "budget_pass": True,
        "baseline_facts": 3,
        "graph_facts": 6,
        "injected": 3,
        "injected_useful": 1,
        "alpha_cut_loss": 0,
    }
    base.update(kwargs)
    return TaskResult(**base)


# --------------------------------------------------------------------------------------
# Wiring: does the bench measure the product, or a copy of it?
# --------------------------------------------------------------------------------------


def test_bench_runs_production_recall_and_not_a_lookalike(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacing the production recall must change what the bench sees.

    If the bench ever grows its own retrieval path, this test goes green while the bench measures a
    product nobody ships — so the sentinel raises rather than returning something plausible.
    """

    def exploded(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("production recall was called")

    monkeypatch.setattr(session_module, "recall_facts", exploded)
    manager = _manager(tmp_path, ["Orion uses the queue."])
    graph = build_recall_graph(manager)

    with pytest.raises(RuntimeError, match="production recall was called"):
        bench._run_task(_task(), manager=manager, graph=graph, seed=42)


def test_arms_differ_only_by_the_graph_and_the_declared_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def recorder(query: str, **kwargs: Any) -> tuple[list[str], str | None]:
        calls.append({"query": query, **kwargs})
        return (["Orion uses the queue."], "keyword")

    monkeypatch.setattr(session_module, "recall_facts", recorder)
    manager = _manager(tmp_path, ["Orion uses the queue."])
    graph = build_recall_graph(manager)
    bench._run_task(_task(), manager=manager, graph=graph, seed=42)

    assert len(calls) == 3
    assert {c["query"] for c in calls} == {"Give me the full picture on Orion."}
    assert {id(c["memory"]) for c in calls} == {id(manager)}
    assert [c["k"] for c in calls] == [3, 3, 6]
    assert [c["graph"] is not None for c in calls] == [False, True, False]
    # The gate is production's, not None: a bench that skips the admission gate measures a recall
    # path the product does not have.
    assert all(c["gate"] is not None for c in calls)


def test_bench_graph_builder_matches_the_production_one(tmp_path: Path) -> None:
    """`build_recall_graph` mirrors `chimera.cli.main._recall_graph`; pin them together.

    The mirror exists so the bench need not import typer/rich. This is the price of the mirror: if
    production changes which memories feed the graph, a test fails instead of the bench quietly
    measuring a graph production never builds.
    """
    from chimera.cli.main import _recall_graph

    manager = _manager(tmp_path, ["Orion uses the queue.", "Vega owns the ledger."])
    manager.add("Draco needs the vault.", provenance="tainted")

    produced = _recall_graph(manager)
    assert produced is not None
    assert {r.as_text() for r in build_recall_graph(manager).relations()} == {
        r.as_text() for r in produced.relations()
    }
    # Tainted memories stay out — entity-linked facts skip the labelling path, so admitting one
    # would put an unlabelled tainted fact in the prompt.
    assert "Draco needs the vault" not in {r.as_text() for r in produced.relations()}


def test_injected_counts_triples_that_reached_the_prompt_not_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`injected` is the treatment's facts minus the baseline's — the graph's real contribution.

    Counting `len(related_facts(...))` instead would inflate it by every triple the dedup against
    the keyword hits already dropped, and "how much did the layer act" is the number the activation
    abort depends on.
    """
    facts = ["Orion uses the queue.", "Orion owns the ledger.", "Orion needs the vault."]

    def recall(_query: str, **kwargs: Any) -> tuple[list[str], str | None]:
        if kwargs["graph"] is None:
            return (facts[:2], "keyword")
        return (facts, "keyword+graph")  # the graph added exactly one NEW fact

    monkeypatch.setattr(session_module, "recall_facts", recall)
    manager = _manager(tmp_path, facts)
    graph = build_recall_graph(manager)
    assert len(graph.related_facts("Give me the full picture on Orion.", k=10)) == 3

    result = bench._run_task(_task(), manager=manager, graph=graph, seed=42)
    assert result.injected == 1


def test_run_seed_actually_calls_the_slice_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard is in the execution path, not merely defined next to it."""

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise GraphBenchError("guard reached")

    monkeypatch.setattr(bench, "_assert_slices_are_what_they_claim", refuse)
    monkeypatch.setattr(bench, "N_ENTITIES", 4)
    monkeypatch.setattr(bench, "N_NOISE", 3)
    with pytest.raises(GraphBenchError, match="guard reached"):
        run_seed(42, tmp_path)


def test_run_graph_bench_actually_calls_the_activation_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_self: GraphBenchReport) -> None:
        raise GraphBenchError("activation check reached")

    monkeypatch.setattr(GraphBenchReport, "check_activation", refuse)
    monkeypatch.setattr(bench, "N_ENTITIES", 4)
    monkeypatch.setattr(bench, "N_NOISE", 3)
    with pytest.raises(GraphBenchError, match="activation check reached"):
        bench.run_graph_bench(tmp_path, seeds=(42,))


# --------------------------------------------------------------------------------------
# Guards, each fed the broken state it exists to catch
# --------------------------------------------------------------------------------------


def test_round_trip_guard_rejects_a_fact_the_extractor_cannot_rebuild() -> None:
    with pytest.raises(GraphBenchError, match="round-trip"):
        bench._assert_round_trips(["Orion contains three replicas."])  # no relation keyword at all


def test_round_trip_guard_rejects_a_fact_that_yields_two_relations() -> None:
    with pytest.raises(GraphBenchError, match="round-trip"):
        bench._assert_round_trips(["Orion uses the queue. Vega owns the ledger."])


def test_round_trip_guard_accepts_every_generated_fact() -> None:
    bench._assert_round_trips(list(build_corpus(42).facts))


def test_reachability_guard_rejects_an_impossible_task(tmp_path: Path) -> None:
    """A reference no budget can reach scores 0% for every arm and looks like a finding."""
    manager = _manager(tmp_path, ["Orion uses the queue.", "Vega owns the ledger."])
    corpus = GraphCorpus(42, ("Orion uses the queue.",), (_task(MULTIHOP, ("Vega owns the ledger.",)),))
    with pytest.raises(GraphBenchError, match="impossible"):
        bench._assert_slices_are_what_they_claim(corpus, manager, build_recall_graph(manager))


def test_reachability_guard_rejects_a_traverse_task_plain_search_can_reach(tmp_path: Path) -> None:
    """If keyword search alone closes it, the slice is not measuring a hop and its null is unearned."""
    manager = _manager(tmp_path, ["Orion uses the queue."])
    corpus = GraphCorpus(42, ("Orion uses the queue.",), (_task(TRAVERSE, ("Orion uses the queue.",)),))
    with pytest.raises(GraphBenchError, match="not measuring a hop"):
        bench._assert_slices_are_what_they_claim(corpus, manager, build_recall_graph(manager))


def test_slice_guard_rejects_an_empty_graph(tmp_path: Path) -> None:
    manager = _manager(tmp_path, ["Orion uses the queue."])
    corpus = GraphCorpus(42, (), ())
    with pytest.raises(GraphBenchError, match="empty"):
        bench._assert_slices_are_what_they_claim(corpus, manager, build_graph([]))


def test_additivity_guard_fires_when_recall_stops_being_purely_additive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`injected` and the structurally-zero McNemar `b` cell both depend on this invariant."""

    def recall(_query: str, **kwargs: Any) -> tuple[list[str], str | None]:
        if kwargs["graph"] is None:
            return (["Orion uses the queue.", "Orion owns the ledger."], "keyword")
        return (["Orion owns the ledger."], "graph")  # a cap would drop a baseline fact

    monkeypatch.setattr(session_module, "recall_facts", recall)
    manager = _manager(tmp_path, ["Orion uses the queue."])
    with pytest.raises(GraphBenchError, match="purely additive"):
        bench._run_task(_task(), manager=manager, graph=build_recall_graph(manager), seed=42)


def test_activation_abort_refuses_a_run_the_graph_sat_out() -> None:
    """A null over turns where the layer never fired reads 'did not help' and means 'was never on'."""
    silent = [_result(task_id=f"t{i}", injected=0) for i in range(10)]
    report = GraphBenchReport(
        (42,),
        {
            MULTIHOP: SliceReport(MULTIHOP, silent),
            SINGLE: SliceReport(SINGLE, []),
            TRAVERSE: SliceReport(TRAVERSE, []),
        },
    )
    with pytest.raises(GraphBenchError, match="never switched on"):
        report.check_activation()


def test_activation_abort_lets_a_run_the_graph_took_part_in_through() -> None:
    loud = [_result(task_id=f"t{i}", injected=2) for i in range(10)]
    report = GraphBenchReport(
        (42,),
        {
            MULTIHOP: SliceReport(MULTIHOP, loud),
            SINGLE: SliceReport(SINGLE, []),
            TRAVERSE: SliceReport(TRAVERSE, []),
        },
    )
    report.check_activation()  # must not raise


def test_runner_refuses_fewer_than_three_seeds() -> None:
    """Two seeds produce a difference, not a variance. The runner says so instead of averaging two."""
    done = subprocess.run(
        [sys.executable, str(REPO_ROOT / "bench" / "memory_graph" / "run_graph.py")],
        env={**os.environ, "BENCH_SEEDS": "42,43"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 2
    assert "three decide" in done.stdout


# --------------------------------------------------------------------------------------
# The gate reads lift and cost together
# --------------------------------------------------------------------------------------


def _report(*, lift_wins: int, control_drops: int, useful: int, injected: int) -> GraphBenchReport:
    multihop = [_result(task_id=f"m{i}", baseline_pass=False, graph_pass=True) for i in range(lift_wins)]
    multihop += [
        _result(task_id=f"n{i}", baseline_pass=True, graph_pass=True, injected=0, injected_useful=0)
        for i in range(100 - lift_wins)
    ]
    control = [
        _result(task_id=f"c{i}", slice=SINGLE, baseline_pass=True, graph_pass=False, injected=0, injected_useful=0)
        for i in range(control_drops)
    ]
    control += [
        _result(task_id=f"d{i}", slice=SINGLE, baseline_pass=True, graph_pass=True, injected=injected, injected_useful=useful)
        for i in range(100 - control_drops)
    ]
    return GraphBenchReport(
        (42,),
        {
            MULTIHOP: SliceReport(MULTIHOP, multihop),
            SINGLE: SliceReport(SINGLE, control),
            TRAVERSE: SliceReport(TRAVERSE, []),
        },
    )


def test_gate_fails_on_cost_even_when_the_lift_is_real() -> None:
    """A layer can help and still not be worth it. Reporting lift alone hides that."""
    report = _report(lift_wins=30, control_drops=0, useful=0, injected=4)
    passed, why = report.gate()
    assert not passed
    assert "injection precision" in why


def test_gate_fails_when_the_lift_is_absent() -> None:
    report = _report(lift_wins=2, control_drops=0, useful=4, injected=4)
    passed, why = report.gate()
    assert not passed
    assert "multihop lift" in why


def test_gate_fails_when_the_control_slice_drops() -> None:
    report = _report(lift_wins=30, control_drops=10, useful=4, injected=4)
    passed, why = report.gate()
    assert not passed
    assert "control slice dropped" in why


def test_gate_passes_when_lift_and_cost_are_both_acceptable() -> None:
    report = _report(lift_wins=30, control_drops=0, useful=4, injected=4)
    passed, why = report.gate()
    assert passed, why


# --------------------------------------------------------------------------------------
# Corpus and statistics
# --------------------------------------------------------------------------------------


def test_bench_items_match_what_manager_add_builds(tmp_path: Path) -> None:
    """The bench writes memories itself only to pin the id. Everything else must be production's.

    `MemoryManager.add` has no id parameter and the random uuid it mints is the keyword ranker's
    tie-break, which is the baseline arm's choice of facts. This test is the price of building the
    item here: if `add` starts setting something else, it fails instead of the bench quietly
    becoming a different write path.
    """
    written = bench._build_manager(GraphCorpus(42, ("Orion uses the queue.",), ()), tmp_path)
    stored = written.store.all()[0]

    reference = MemoryManager(MemoryStore(tmp_path / "reference.json")).add("Orion uses the queue.")
    assert stored.model_dump(exclude={"id"}) == reference.model_dump(exclude={"id"})
    assert stored.id != reference.id  # the one field the bench controls


def test_a_seed_fully_determines_a_run(tmp_path: Path) -> None:
    """Same seed, two runs, identical results — including the tie-break the uuids used to randomise."""
    first = run_seed(42, tmp_path / "a")
    second = run_seed(42, tmp_path / "b")
    assert [(r.task_id, r.baseline_pass, r.graph_pass, r.budget_pass) for r in first] == [
        (r.task_id, r.baseline_pass, r.graph_pass, r.budget_pass) for r in second
    ]


def test_corpus_is_reproducible_per_seed_and_really_changes_between_seeds() -> None:
    assert build_corpus(42).facts == build_corpus(42).facts
    assert build_corpus(42).facts != build_corpus(43).facts


def test_every_slice_item_is_a_distinct_entity() -> None:
    """Count distinct items, not rows: a slice of repeats reports a confidence interval it has not earned."""
    corpus = build_corpus(42)
    for name in (MULTIHOP, SINGLE, TRAVERSE):
        tasks = corpus.slice_tasks(name)
        assert len(tasks) == bench.N_ENTITIES
        assert len({t.entity for t in tasks}) == len(tasks)


def test_multihop_and_traverse_share_the_query_so_only_the_answer_differs() -> None:
    """Two slices out of one generator: nothing superficial separates them but what an answer needs."""
    corpus = build_corpus(42)
    wide = {t.entity: t.query for t in corpus.slice_tasks(MULTIHOP)}
    assert all(t.query == wide[t.entity] for t in corpus.slice_tasks(TRAVERSE))


def test_paired_delta_se_matches_the_mcnemar_formula() -> None:
    from chimera.eval.paired import compare_paired

    result = compare_paired([False] * 10 + [True] * 10, [True] * 16 + [False] * 4)
    b, c, n = result.baseline_only, result.treatment_only, result.n
    expected = ((b + c - (c - b) ** 2 / n) ** 0.5) / n
    assert paired_delta_se(result) == pytest.approx(expected)


def test_paired_delta_se_is_zero_when_the_arms_never_disagreed() -> None:
    from chimera.eval.paired import compare_paired

    assert paired_delta_se(compare_paired([True] * 8, [True] * 8)) == 0.0


def test_one_seed_end_to_end_produces_the_three_slices(tmp_path: Path) -> None:
    """The real corpus, the real store, the real recall — the smoke test that the whole thing runs."""
    results = run_seed(42, tmp_path)
    assert len(results) == 3 * bench.N_ENTITIES
    assert {r.slice for r in results} == {MULTIHOP, SINGLE, TRAVERSE}
    multihop = SliceReport(MULTIHOP, [r for r in results if r.slice == MULTIHOP])
    assert multihop.activation_rate() >= GraphBenchReport.MIN_ACTIVATION_RATE
    # `traverse` is the mechanism ceiling: `related_facts` does not walk an edge, so no arm closes it.
    assert not any(r.graph_pass for r in results if r.slice == TRAVERSE)
