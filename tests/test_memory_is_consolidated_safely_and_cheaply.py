"""P1-13 (audit 2026-11, a05-06/10/11/12): memory that survives a failure, a second process and a
repeat pass without paying again.

Each test here fails on the code before the change:

* consolidation deleted a cluster's members and THEN wrote the summary, so a failure between the two
  steps lost the facts; and it re-asked the model about every cluster on every pass, including the
  ones it had answered with nothing;
* the SQLite store used the default rollback journal and a 5 s busy timeout;
* the semantic index lived one request, so each search re-embedded the corpus;
* `chimera migrate --apply` built a bare manager, so its writes left no audit record.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from chimera.evolution.wiring import build_memory_manager
from chimera.memory.consolidate import ConsolidationLedger
from chimera.memory.manager import MemoryManager
from chimera.memory.models import MemoryItem
from chimera.memory.semantic import SemanticIndex
from chimera.memory.sqlite_store import SqliteMemoryStore
from chimera.memory.store import MemoryStore

TWINS = [
    "the deploy pipeline runs on github actions every merge",
    "the deploy pipeline runs on github actions after every merge",
    "deploy pipeline runs on github actions every merge to main",
]


def _manager(tmp_path: Path, **kwargs: object) -> MemoryManager:
    return MemoryManager(MemoryStore(tmp_path / "memory.json"), **kwargs)  # type: ignore[arg-type]


class _Summarizer:
    def __init__(self, answer: str = "the deploy pipeline runs on github actions on every merge") -> None:
        self.answer = answer
        self.calls = 0

    def __call__(self, facts: list[str]) -> str:
        self.calls += 1
        return self.answer


# --- 1. the summary is written before the originals go -------------------------------------------


def test_a_failure_writing_the_summary_loses_no_fact(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    originals = [mgr.add(text) for text in TWINS]

    def broken_add(self: MemoryManager, *a: object, **k: object) -> MemoryItem:
        raise OSError("disk full")

    real_add = MemoryManager.add
    MemoryManager.add = broken_add  # type: ignore[method-assign]
    try:
        with pytest.raises(OSError):
            mgr.consolidate(_Summarizer())
    finally:
        MemoryManager.add = real_add  # type: ignore[method-assign]

    assert {i.id for i in mgr.store.all()} == {i.id for i in originals}


def test_a_failure_removing_the_originals_loses_no_fact(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    originals = [mgr.add(text) for text in TWINS]
    real_remove = mgr.store.remove
    calls = {"n": 0}

    def flaky_remove(item_id: str) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("database is locked")
        real_remove(item_id)

    mgr.store.remove = flaky_remove  # type: ignore[method-assign]
    with pytest.raises(OSError):
        mgr.consolidate(_Summarizer("SUMMARY of the deploy facts"))

    remaining = mgr.store.all()
    contents = {i.content for i in remaining}
    assert "SUMMARY of the deploy facts" in contents
    # Nothing was lost: every original is either still there or covered by the summary that already
    # exists. Before the fix the first original was gone and no summary had been written.
    survivors = {i.id for i in remaining}
    gone = [o for o in originals if o.id not in survivors]
    assert len(gone) <= 1 and "SUMMARY of the deploy facts" in contents


# --- blank clusters are remembered, and a repeat pass asks nothing -------------------------------


def test_a_blank_cluster_is_not_paid_for_twice(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    for text in TWINS:
        mgr.add(text)
    blank = _Summarizer("")
    first = mgr.consolidate_outcome(blank)
    second = mgr.consolidate_outcome(blank)
    assert (first.blank, blank.calls) == (1, 1)
    assert second.blank == 0 and second.unchanged == 1
    assert blank.calls == 1  # the second pass made no model call


def test_a_changed_cluster_is_asked_again(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    items = [mgr.add(text) for text in TWINS]
    blank = _Summarizer("")
    mgr.consolidate_outcome(blank)
    mgr.edit(items[0].id, "the deploy pipeline runs on github actions each merge, edited")
    mgr.consolidate_outcome(blank)
    assert blank.calls == 2


def test_a_second_pass_with_no_new_write_makes_no_model_call(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    for text in TWINS:
        mgr.add(text)
    mgr.add("the invoice export is a csv with semicolons")
    summarizer = _Summarizer()
    assert mgr.consolidate(summarizer) == 2
    calls_after_first = summarizer.calls
    assert calls_after_first == 1
    assert mgr.consolidate(summarizer) == 0
    assert summarizer.calls == calls_after_first


def test_the_ledger_survives_the_process(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    first = _manager(tmp_path, ledger=ConsolidationLedger(path))
    for text in TWINS:
        first.add(text)
    blank = _Summarizer("")
    first.consolidate(blank)
    # A new manager over the same files: what the old one settled is still settled.
    second = MemoryManager(MemoryStore(tmp_path / "memory.json"), ledger=ConsolidationLedger(path))
    second.consolidate(blank)
    assert blank.calls == 1


# --- 3. WAL and busy_timeout ---------------------------------------------------------------------


def test_the_sqlite_store_runs_in_wal_with_a_busy_timeout(tmp_path: Path) -> None:
    store = SqliteMemoryStore(tmp_path / "m.db")
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    timeout = store._conn.execute("PRAGMA busy_timeout").fetchone()[0]
    store.close()
    assert str(mode).lower() == "wal"
    assert int(timeout) >= 10_000


def test_two_processes_writing_500_facts_each_leave_1000_rows(tmp_path: Path) -> None:
    db = tmp_path / "shared.db"
    SqliteMemoryStore(db).close()  # schema exists before the race: the race under test is the writes
    script = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        from chimera.memory.models import MemoryItem
        from chimera.memory.sqlite_store import SqliteMemoryStore

        tag, db = sys.argv[1], Path(sys.argv[2])
        store = SqliteMemoryStore(db)
        for n in range(500):
            store.add(MemoryItem(id=f"{tag}-{n}", content=f"fact {tag} {n}"))
        store.close()
        """
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script, tag, str(db)],
            stderr=subprocess.PIPE, text=True,
        )
        for tag in ("a", "b")
    ]
    errors = [p.communicate(timeout=120)[1] for p in procs]
    assert [p.returncode for p in procs] == [0, 0], errors
    store = SqliteMemoryStore(db)
    try:
        assert len(store) == 1000
    finally:
        store.close()


# --- 4. migration goes through build_memory_manager and is audited -------------------------------


def test_a_migration_apply_is_chained_into_the_audit_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from chimera.cli.main import app

    source = tmp_path / "claude"
    (source / "memory").mkdir(parents=True)
    (source / "memory" / "notes.md").write_text(
        "- The customer prefers invoices on the first of the month\n", encoding="utf-8"
    )
    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    result = CliRunner().invoke(app, ["migrate", "claude", str(source), "--apply", "--home", str(home)])
    assert result.exit_code == 0, result.output
    audit = home / "audit.jsonl"
    assert audit.exists()
    events = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any("memory_add" in json.dumps(e) for e in events)


# --- 5. vectors persisted by the hash of the text ------------------------------------------------


class _CountingEmbed:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [[float(len(t) % 7), float(sum(map(ord, t)) % 13), 1.0] for t in texts]


def test_the_second_search_does_not_call_the_embedder(tmp_path: Path) -> None:
    items = [MemoryItem(id=str(n), content=f"fact number {n} about deploys") for n in range(20)]
    cache = tmp_path / "vectors.db"
    first_embed = _CountingEmbed()
    first = SemanticIndex(first_embed, cache_path=cache, namespace="m1")
    top = first.search("what about deploys", items, 3)
    assert len(first_embed.texts) == 21  # 20 facts + the query

    # A NEW index (a new request, a new process) over the same file: nothing to embed.
    second_embed = _CountingEmbed()
    second = SemanticIndex(second_embed, cache_path=cache, namespace="m1")
    assert [i.id for i in second.search("what about deploys", items, 3)] == [i.id for i in top]
    assert second_embed.texts == []


def test_a_different_embedding_model_does_not_reuse_the_vectors(tmp_path: Path) -> None:
    items = [MemoryItem(id="1", content="a fact about deploys")]
    cache = tmp_path / "vectors.db"
    SemanticIndex(_CountingEmbed(), cache_path=cache, namespace="small").search("q", items, 1)
    other = _CountingEmbed()
    SemanticIndex(other, cache_path=cache, namespace="large").search("q", items, 1)
    assert other.texts, "vectors from another model must not answer for this one"


def test_a_broken_vector_cache_costs_an_embed_not_the_search(tmp_path: Path) -> None:
    bad = tmp_path / "vectors.db"
    bad.write_bytes(b"this is not a sqlite file at all" * 20)
    embed = _CountingEmbed()
    items = [MemoryItem(id="1", content="a fact about deploys")]
    assert SemanticIndex(embed, cache_path=bad).search("deploys", items, 1) == items


def test_build_memory_manager_wires_ledger_and_vectors_beside_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.config import Settings

    monkeypatch.setenv("CHIMERA_SEMANTIC_MEMORY", "1")
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))  # `home=` is ignored: the field is env-aliased
    settings = Settings()
    mgr = build_memory_manager(settings)
    assert mgr._semantic is not None and mgr._semantic._disk is not None
    for text in TWINS:
        mgr.add(text)
    mgr.consolidate(_Summarizer(""))
    assert (tmp_path / "memory_consolidation.json").exists()


# --- 6/7 (backend half): which facts a turn used, and similar pairs ------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from chimera.config import Settings, get_settings
    from chimera.interface import ChatSession

    class _Agent:
        def run(self, task: str, **_k: object) -> object:
            raise AssertionError("no chat in these tests")

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    get_settings.cache_clear()
    from chimera.api import build_api_app

    yield TestClient(build_api_app(lambda: ChatSession(_Agent()), settings=Settings()))
    get_settings.cache_clear()


def test_recall_hands_back_the_records_and_forgetting_one_keeps_it_out_of_the_next_turn(
    client, tmp_path: Path  # type: ignore[no-untyped-def]
) -> None:
    from chimera.config import Settings
    from chimera.interface.session import recall_facts

    ids = []
    for text, provenance in (
        ("the staging database lives on port 5433", "clean"),
        ("the staging database password rotates monthly", "tainted"),
        ("the staging database backups run at night", "clean"),
    ):
        mgr = build_memory_manager(Settings())
        ids.append(mgr.add(text, provenance=provenance, source="chat").id)

    def turn() -> list[MemoryItem]:
        seen: list[MemoryItem] = []
        recall_facts(
            "tell me about the staging database",
            memory=build_memory_manager(Settings()), gate=None, k=5, on_items=seen.extend,
        )
        return seen

    first = turn()
    assert len(first) == 3
    assert sorted(i.provenance for i in first) == ["clean", "clean", "tainted"]

    assert client.delete(f"/api/memory/{ids[1]}").json() == {"deleted": True}
    second = turn()
    assert ids[1] not in {i.id for i in second} and len(second) == 2


def test_similar_pairs_lists_near_duplicates_from_different_days_only(tmp_path: Path) -> None:
    clock = {"now": 1_700_000_000.0}
    mgr = MemoryManager(MemoryStore(tmp_path / "m.json"), clock=lambda: clock["now"])
    old = mgr.add("the release train leaves every thursday at noon")
    clock["now"] += 3 * 86_400
    new = mgr.add("the release train leaves every friday at noon")  # a correction, three days on
    mgr.add("the release train leaves every friday at noon sharp")  # same day as `new`: not a pair with it
    mgr.add("completely unrelated note about invoices")

    pairs = mgr.similar_pairs(threshold=0.6)
    found = {frozenset((a.id, b.id)) for a, b, _ in pairs}
    assert frozenset((old.id, new.id)) in found
    # Same-day twins are one statement stored twice, not an old claim and its correction.
    same_day = [p for p in pairs if {p[0].content, p[1].content} == {new.content, "the release train leaves every friday at noon sharp"}]
    assert same_day == []


def test_the_similar_route_returns_the_pair_and_resolving_removes_the_chosen_one(
    client, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]
) -> None:
    from chimera.config import Settings

    clock = {"now": 1_700_000_000.0}
    mgr = build_memory_manager(Settings())
    mgr._clock = lambda: clock["now"]
    old = mgr.add("the release train leaves every thursday at noon")
    clock["now"] += 3 * 86_400
    new = mgr.add("the release train leaves every friday at noon")

    body = client.get("/api/memory/similar").json()
    assert len(body["pairs"]) == 1
    pair = body["pairs"][0]
    assert {pair["a"]["id"], pair["b"]["id"]} == {old.id, new.id}
    assert pair["a"]["created_at"] != pair["b"]["created_at"]

    client.delete(f"/api/memory/{old.id}")
    assert client.get("/api/memory/similar").json()["pairs"] == []
    assert {i["id"] for i in client.get("/api/memory").json()} == {new.id}
