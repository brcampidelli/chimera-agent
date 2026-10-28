"""The repo map spends its budget on the files that matter — including in THIS repository.

The map used to sort alphabetically and truncate. Measured here, that meant 48 lines survived, 473
files were dropped, and ``apps/desktop/src-tauri/build_sidecar.py`` made the cut while
``chimera/core/agent.py`` did not. An agent handed that map was worse off than one handed nothing,
because it looked like a map.

The first test below is the one that matters: it runs against the real tree, not a fixture, so it
cannot be satisfied by a scenario built to be satisfiable. It would have failed before the ranking
existed, and it will fail again the day the ranking regresses.

Nothing here asserts an exact ordering between two similarly-central files. PageRank on a real graph
is a continuous score and near-ties move with ordinary refactors; a test that pinned position 3
would fail for reasons that are not defects, and would be deleted the second time it did.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.core.repomap import _cache_path, build_repo_map

ROOT = Path(__file__).resolve().parent.parent


def _rank_of(digest: str, path: str) -> int:
    """Where a file appears in the digest. -1 when it did not survive the budget at all."""
    for i, line in enumerate(digest.splitlines()):
        if line.startswith(f"{path}:") or line == path:
            return i
    return -1


def test_this_repositorys_core_survives_the_budget() -> None:
    """The files an agent working here has to find, in the map's DEFAULT budget.

    Under the old alphabetical sort, ``chimera/core/agent.py`` and ``chimera/core/autonomous.py``
    were dropped while ``apps/desktop/src-tauri/build_sidecar.py`` survived. ``chimera/api/app.py``
    is here for a second reason: under *directed-only* PageRank it sat at position 127, because
    nothing imports an entry point. Both failure modes are pinned.

    ``chimera/fusion/engine.py`` was pinned too, and is no longer: measured on 2026-10-16, its place
    rested on one edge. ``providers/catalog.py`` had a single internal import, ``fusion/receipts.py``,
    which imports the catalog back; that cycle gave receipts 92% of its score, and receipts supplied
    68% of the engine's. When the catalog gained three legitimate imports (the deployment policy,
    discovery, the gateway) the engine fell from 25th to 50th without anything about it changing.
    By the graph it has eight product importers, 60th by that measure. A pin that only held because
    of a cycle in another module asserts something the import structure does not support.

    2026-10-28: ``chimera/core/autonomous.py`` moved from budget membership to rank. Membership at the
    default budget rested on how long the OTHER lines were: a new module imported by
    ``chimera/design/model.py`` ranks 19th, its 203-character line pushed autonomous.py (rank 23) just
    past the budget, and the test failed with autonomous.py's own rank unchanged. What the pin exists
    to catch is the ranking regressing, so it is asserted on a budget large enough to hold the whole
    ranking. Under the old alphabetical sort autonomous.py sat at index 263 of the map's 1,222
    entries, so the bound of 40 still catches that regression.
    """
    digest = build_repo_map(ROOT)
    for path in ("chimera/core/agent.py", "chimera/api/app.py"):
        assert _rank_of(digest, path) >= 0, f"{path} did not survive the budget — ranking regressed"

    ranked = build_repo_map(ROOT, max_chars=200_000)
    rank = _rank_of(ranked, "chimera/core/autonomous.py")
    assert 0 <= rank < 40, f"chimera/core/autonomous.py ranked {rank} — ranking regressed"


def test_a_widely_imported_module_outranks_a_leaf(tmp_path: Path) -> None:
    """The mechanism, stated on a graph small enough to reason about by hand.

    ``core.py`` is imported by ten modules and ``aaa_lonely.py`` by none. Asserted as an ordering
    rather than as membership, because "did it fit in N characters" depends on how long the other
    lines happened to be, and a test that fails when someone renames a fixture file is a test that
    gets deleted. Under an alphabetical sort this still fails — ``aaa_lonely`` would come first.
    """
    (tmp_path / "core.py").write_text("def engine(): ...\n", encoding="utf-8")
    (tmp_path / "aaa_lonely.py").write_text("def nobody_calls_me(): ...\n", encoding="utf-8")
    for i in range(10):
        (tmp_path / f"user_{i}.py").write_text("from core import engine\n", encoding="utf-8")

    digest = build_repo_map(tmp_path)
    assert _rank_of(digest, "core.py") < _rank_of(digest, "aaa_lonely.py")


@pytest.mark.parametrize(
    "facade",
    [
        "from pkg.engine import Engine\nfrom pkg.aaa_other import Other\n",
        "from .engine import Engine\nfrom .aaa_other import Other\n",
        # The lazy (PEP 562) shape this repository's own facades use: the static truth lives under
        # TYPE_CHECKING and the runtime lookup goes through `__getattr__`.
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from pkg.engine import Engine\n"
        "    from pkg.aaa_other import Other\n"
        "def __getattr__(name): ...\n",
    ],
    ids=["absolute", "relative", "lazy"],
)
def test_an_import_through_a_facade_credits_the_module_that_defines_the_name(
    tmp_path: Path, facade: str
) -> None:
    """``from pkg import Engine`` leans on ``pkg/engine.py``, not on ``pkg/__init__.py``.

    The edge used to stop at the facade, which then passed its rank on to EVERY submodule it
    re-exports, equally. So ``engine.py``, which six modules import, tied with ``aaa_other.py``,
    which none do — and lost the tie on the alphabet. Counted on this repository there were over
    five hundred product imports like that, and the files they credited wrongly included
    ``chimera/core/autonomous.py``, whose absence from the map is what the first test here catches.

    Asserted twice because the second build is served from the parse cache: a cache entry that lost
    the facade's re-exports would quietly bring the old ranking back.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(facade, encoding="utf-8")
    (pkg / "engine.py").write_text("class Engine: ...\n", encoding="utf-8")
    (pkg / "aaa_other.py").write_text("class Other: ...\n", encoding="utf-8")
    for i in range(6):
        (tmp_path / f"user_{i}.py").write_text("from pkg import Engine\n", encoding="utf-8")

    for _ in range(2):
        digest = build_repo_map(tmp_path)
        assert _rank_of(digest, "pkg/engine.py") < _rank_of(digest, "pkg/aaa_other.py")


def test_a_cache_written_before_re_exports_were_recorded_is_not_served(tmp_path: Path) -> None:
    """An entry from the older scanner has no ``reexports`` and must miss, not read back as a facade
    that re-exports nothing — that would restore the old ranking until each ``__init__`` changed."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from pkg.engine import Engine\nfrom pkg.aaa_other import Other\n", encoding="utf-8"
    )
    (pkg / "engine.py").write_text("class Engine: ...\n", encoding="utf-8")
    (pkg / "aaa_other.py").write_text("class Other: ...\n", encoding="utf-8")
    for i in range(6):
        (tmp_path / f"user_{i}.py").write_text("from pkg import Engine\n", encoding="utf-8")
    build_repo_map(tmp_path)

    cache = _cache_path(tmp_path.resolve())
    assert cache is not None and cache.is_file()
    entries = json.loads(cache.read_text(encoding="utf-8"))
    for rel, entry in entries.items():  # rewrite exactly as the previous scanner left it
        stat = (tmp_path / rel).stat()
        entry["key"] = f"{stat.st_mtime_ns}:{stat.st_size}"
        entry.pop("reexports", None)
    cache.write_text(json.dumps(entries), encoding="utf-8")

    digest = build_repo_map(tmp_path)
    assert _rank_of(digest, "pkg/engine.py") < _rank_of(digest, "pkg/aaa_other.py")


def test_a_name_the_facade_does_not_export_adds_no_edge(tmp_path: Path) -> None:
    """Following a facade must never invent an edge: a wrong edge moves rank, a missing one only
    fails to. ``Ghost`` is defined in ``pkg/engine.py`` but the facade does not re-export it, so an
    import of ``pkg.Ghost`` (which would fail at runtime) credits nothing below the facade."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from pkg.aaa_other import Other\n", encoding="utf-8")
    (pkg / "engine.py").write_text("class Ghost: ...\n", encoding="utf-8")
    (pkg / "aaa_other.py").write_text("class Other: ...\n", encoding="utf-8")
    for i in range(6):
        (tmp_path / f"user_{i}.py").write_text("from pkg import Ghost\n", encoding="utf-8")

    digest = build_repo_map(tmp_path)
    assert _rank_of(digest, "pkg/aaa_other.py") < _rank_of(digest, "pkg/engine.py")


def test_the_task_pulls_its_own_files_up(tmp_path: Path) -> None:
    """Personalisation: naming a file in the task outranks the graph's own opinion.

    Without it the map answers "what does this repo depend on?", which is the right question exactly
    once — before the agent knows what it is doing.
    """
    (tmp_path / "hub.py").write_text("def hub(): ...\n", encoding="utf-8")
    (tmp_path / "widget.py").write_text("def widget(): ...\n", encoding="utf-8")
    for i in range(6):
        (tmp_path / f"dep_{i}.py").write_text("from hub import hub\n", encoding="utf-8")

    plain = build_repo_map(tmp_path)
    assert _rank_of(plain, "widget.py") > _rank_of(plain, "hub.py")

    aimed = build_repo_map(tmp_path, task="fix the widget rendering")
    assert _rank_of(aimed, "widget.py") < _rank_of(aimed, "hub.py")


def test_focus_beats_a_task_that_never_names_the_file(tmp_path: Path) -> None:
    """``focus`` is for the caller that KNOWS (the open file, the last edit) rather than guesses."""
    (tmp_path / "hub.py").write_text("def hub(): ...\n", encoding="utf-8")
    (tmp_path / "obscure.py").write_text("def thing(): ...\n", encoding="utf-8")
    for i in range(6):
        (tmp_path / f"dep_{i}.py").write_text("from hub import hub\n", encoding="utf-8")

    digest = build_repo_map(tmp_path, task="make it work", focus=["obscure.py"])
    assert _rank_of(digest, "obscure.py") < _rank_of(digest, "hub.py")


def test_typescript_exports_are_mapped(tmp_path: Path) -> None:
    """A repository whose front end is invisible to its own map is half a map."""
    (tmp_path / "api.ts").write_text(
        "export function fetchRun() {}\nexport interface RunOut { id: string }\n", encoding="utf-8"
    )
    digest = build_repo_map(tmp_path)
    assert "api.ts: fetchRun, RunOut" in digest


def test_relative_typescript_imports_carry_rank(tmp_path: Path) -> None:
    """Relative specifiers resolve; path aliases (`@/lib/x`) deliberately do not, because guessing
    a tsconfig would produce wrong edges, and a wrong edge moves rank."""
    (tmp_path / "lib.ts").write_text("export const shared = 1\n", encoding="utf-8")
    (tmp_path / "alone.ts").write_text("export const alone = 1\n", encoding="utf-8")
    for i in range(8):
        (tmp_path / f"page_{i}.tsx").write_text("import { shared } from './lib'\n", encoding="utf-8")

    digest = build_repo_map(tmp_path)
    assert _rank_of(digest, "lib.ts") < _rank_of(digest, "alone.ts")


def test_a_single_huge_file_cannot_eat_the_whole_budget(tmp_path: Path) -> None:
    """One module with two hundred exports must not crowd out every other file's line."""
    huge = "\n".join(f"def symbol_number_{i}(): ..." for i in range(200))
    (tmp_path / "huge.py").write_text(huge + "\n", encoding="utf-8")
    (tmp_path / "small.py").write_text("def tiny(): ...\n", encoding="utf-8")

    digest = build_repo_map(tmp_path, max_chars=2000)
    huge_line = next(line for line in digest.splitlines() if line.startswith("huge.py"))
    assert len(huge_line) <= 240
    assert "(+" in huge_line  # says how many it dropped rather than dropping them silently
    assert "small.py: tiny()" in digest


def test_the_digest_is_byte_stable_across_runs(tmp_path: Path) -> None:
    """A map that reshuffles itself between two identical runs poisons the prompt cache for free."""
    for i in range(12):
        (tmp_path / f"m{i}.py").write_text(f"def f{i}(): ...\n", encoding="utf-8")
    assert build_repo_map(tmp_path) == build_repo_map(tmp_path)


def test_the_cache_does_not_serve_a_stale_file(tmp_path: Path, monkeypatch: object) -> None:
    """The cache key is the file's own mtime and size, so a changed file cannot hit it. Asserted
    because a repo map that reports symbols a file no longer has is worse than a slow one."""
    target = tmp_path / "mod.py"
    target.write_text("def before(): ...\n", encoding="utf-8")
    assert "before()" in build_repo_map(tmp_path)

    target.write_text("def after(): ...\ndef also_after(): ...\n", encoding="utf-8")
    digest = build_repo_map(tmp_path)
    assert "after()" in digest and "before()" not in digest


def test_an_agent_worktree_never_enters_the_map(tmp_path: Path) -> None:
    """A nested checkout is not part of the repository, and the map must not think it is.

    Agent harnesses put isolated worktrees under `.claude/worktrees/`, which puts one FULL COPY of
    the repository inside the repository per concurrent agent. Measured on this tree with four of
    them running: 9,870 extra Python files, and `chimera/api/app.py` dropped out of the map's budget
    entirely — the same file whose disappearance the first test in this module exists to catch.

    The DIRECTION is what makes it worth pinning. The map does not shrink and does not error; it
    stays the same size and quietly fills with duplicates, so the agent receives something that
    looks like a map while the file it needed is missing. The only symptom is an agent looking in
    the wrong place, which nobody reports as a bug.

    What is asserted here is the containment, not the crowding. Reproducing the crowd-out
    synthetically needs a fixture as lopsided as the real repository, and two earlier versions of
    this test passed against the bug while pretending otherwise — one because fifty short files do
    not fill a 4000-character budget, and one because it looked for a `".claude/"` prefix the map
    never prints (paths arrive as `claude/worktrees/...`, no leading dot). The crowd-out is real and
    was measured; it is documented above rather than faked below.
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text("def real() -> None: ...\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from pkg.core import real\n", encoding="utf-8")

    clone = tmp_path / ".claude" / "worktrees" / "agent-1" / "pkg"
    clone.mkdir(parents=True)
    body = "".join(f"def sym_{i}() -> None: ...\n" for i in range(12))
    for i in range(300):
        (clone / f"copy{i}.py").write_text(body, encoding="utf-8")

    digest = build_repo_map(tmp_path)

    # Matched on the substring rather than a prefix: the map strips the leading dot, and pinning the
    # exact rendering would make this pass again the day that changes.
    assert "worktrees/agent-1" not in digest, "a copy inside .claude reached the map"
    assert _rank_of(digest, "pkg/core.py") >= 0, "the real file must still be there"
