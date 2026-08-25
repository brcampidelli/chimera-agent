"""Memory scoped to the folder it was learned in.

The case that asked for this was real, and small: a desktop app with exactly two facts stored, one
of them *"the Cafe Aurora test project lives in Desktop/teste-chimera/…"*. That note arrived as
context on ordinary coding requests in a completely different repository. Scoping is not an
optimisation here — an unrelated project's note in the prompt is the model being told something
false about where it is.

The shape:

* a fact carries the project it was learned in, or ``None`` for one that belongs everywhere;
* a turn recalls its own project's facts plus the global ones;
* ``EVERY_PROJECT`` — the default — filters nothing, which is what the memory browser, the CLI
  search and the MCP tool all want, and what keeps every pre-existing caller behaving as it did;
* a fact written before the field existed has no project, so it is global. Nobody loses a memory by
  updating, and that is asserted below rather than assumed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.memory.manager import MemoryManager
from chimera.memory.models import EVERY_PROJECT
from chimera.memory.sqlite_store import SqliteMemoryStore
from chimera.memory.store import MemoryStore

AURORA = "o projeto Cafe Aurora fica em Desktop/teste-chimera"
CHIMERA = "o projeto Chimera usa pytest para os testes"
ESTILO = "prefiro respostas curtas e diretas"

PROJETO_A = "/home/alguem/aurora"
PROJETO_B = "/home/alguem/chimera"


def _store(tmp_path: Path, backend: str) -> MemoryManager:
    if backend == "sqlite":
        return MemoryManager(SqliteMemoryStore(tmp_path / "m.db"))
    return MemoryManager(MemoryStore(tmp_path / "m.json"))


@pytest.fixture(params=["json", "sqlite"])
def mgr(request, tmp_path: Path) -> MemoryManager:
    """Both backends, every test. Scoping decides what a run can SEE, so it must not depend on
    which store the owner picked — the same argument that made `provenance` a real column."""
    m = _store(tmp_path, request.param)
    m.remember(AURORA, project=PROJETO_A)
    m.remember(CHIMERA, project=PROJETO_B)
    m.remember(ESTILO, "persona")  # no project: belongs everywhere
    return m


def _conteudos(items: list) -> set[str]:
    return {i.content for i in items}


def test_a_turn_does_not_see_another_projects_note(mgr: MemoryManager) -> None:
    """The measured complaint, in one line."""
    achados = _conteudos(mgr.search("projeto", k=5, project=PROJETO_B))

    assert AURORA not in achados, "a note about another project arrived as context"
    assert CHIMERA in achados, "the project's own note did not arrive"


def test_a_fact_that_belongs_everywhere_arrives_in_every_project(mgr: MemoryManager) -> None:
    """Scoping must not lock away the facts that were never about a project.

    How somebody likes to be answered is about them. If narrowing swallowed that, the feature would
    have traded one wrong context for a missing one.
    """
    for projeto in (PROJETO_A, PROJETO_B):
        assert ESTILO in _conteudos(mgr.search("respostas curtas", k=5, project=projeto))


def test_no_folder_open_sees_only_what_belongs_everywhere(mgr: MemoryManager) -> None:
    """A conversation with no project is not "every project" — it is no project."""
    achados = _conteudos(mgr.search("projeto respostas", k=5, project=None))

    assert ESTILO in achados
    assert AURORA not in achados and CHIMERA not in achados


def test_the_browser_and_the_cli_still_see_everything(mgr: MemoryManager) -> None:
    """Three of the four callers want no filter, which is why no filter is the default.

    Searching your own memory from the Memory screen and finding only the folder you happen to have
    open would be a worse surprise than the noise this fixes.
    """
    achados = _conteudos(mgr.search("projeto respostas", k=9))

    assert {AURORA, CHIMERA, ESTILO} <= achados, "the unfiltered default stopped being unfiltered"
    assert achados == _conteudos(mgr.search("projeto respostas", k=9, project=EVERY_PROJECT))


def test_facts_written_before_this_field_existed_are_global(tmp_path: Path) -> None:
    """The migration, asserted rather than assumed.

    A store on disk from an older version has no `project` key at all. Read back, those facts must
    be global — the behaviour they always effectively had — so updating loses nobody's memory.
    """
    antigo = [
        {
            "id": "a1",
            "kind": "semantic",
            "content": AURORA,
            "key": None,
            "source": "chimera",
            "provenance": "clean",
            "metadata": {},
        }
    ]
    caminho = tmp_path / "m.json"
    caminho.write_text(json.dumps(antigo), encoding="utf-8")

    mgr = MemoryManager(MemoryStore(caminho))
    (item,) = mgr.store.all()

    assert item.project is None
    assert AURORA in _conteudos(mgr.search("projeto", k=5, project="/qualquer/outro"))


def test_scoping_happens_before_the_cut_not_after(tmp_path: Path) -> None:
    """Why the filter is applied to the candidates rather than to the results.

    Filtering afterwards lets ``k`` fill with other folders' facts and return fewer than asked for —
    or nothing at all — while the relevant ones sit just below the cut. Ten decoys in another
    project, one real fact here, and ``k=3``.
    """
    mgr = MemoryManager(MemoryStore(tmp_path / "m.json"))
    for i in range(10):
        mgr.remember(f"projeto decoy numero {i} com muitas palavras", project="/outro")
    mgr.remember("projeto verdadeiro com muitas palavras aqui", project=PROJETO_B)

    achados = _conteudos(mgr.search("projeto muitas palavras", k=3, project=PROJETO_B))

    assert achados == {"projeto verdadeiro com muitas palavras aqui"}


def test_a_sqlite_store_from_before_the_column_still_opens_and_stays_global(tmp_path: Path) -> None:
    """The migration on the backend that needs one, exercised against a real old table.

    The JSON store gains the field for free — a missing key is the default. SQLite does not: the
    column has to be created, and an FTS5 virtual table cannot ALTER, so it is rebuilt. Getting
    that wrong loses every stored memory silently, which is why this builds the old schema by hand
    rather than trusting that the code path is reachable.
    """
    import sqlite3

    caminho = tmp_path / "antigo.db"
    conn = sqlite3.connect(str(caminho))
    conn.execute(
        "CREATE VIRTUAL TABLE memories USING fts5("
        "id UNINDEXED, kind UNINDEXED, content, key UNINDEXED, source UNINDEXED, "
        "metadata UNINDEXED, provenance UNINDEXED)"
    )
    conn.execute(
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("v1", "semantic", AURORA, "", "chimera", "{}", "clean"),
    )
    conn.commit()
    conn.close()

    mgr = MemoryManager(SqliteMemoryStore(caminho))
    guardados = mgr.store.all()

    assert len(guardados) == 1, "the migration lost the stored memory"
    assert guardados[0].content == AURORA
    assert guardados[0].project is None, "an old fact was scoped to a folder nobody chose"
    assert AURORA in _conteudos(mgr.search("projeto", k=5, project="/qualquer/outro"))
