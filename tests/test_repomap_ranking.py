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

from pathlib import Path

from chimera.core.repomap import build_repo_map

ROOT = Path(__file__).resolve().parent.parent


def _rank_of(digest: str, path: str) -> int:
    """Where a file appears in the digest. -1 when it did not survive the budget at all."""
    for i, line in enumerate(digest.splitlines()):
        if line.startswith(f"{path}:") or line == path:
            return i
    return -1


def test_this_repositorys_core_survives_the_budget() -> None:
    """The four files an agent working here has to find, in the map's DEFAULT budget.

    Under the old alphabetical sort, ``chimera/core/agent.py``, ``chimera/core/autonomous.py`` and
    ``chimera/fusion/engine.py`` were all dropped while ``apps/desktop/src-tauri/build_sidecar.py``
    survived. ``chimera/api/app.py`` is here for a second reason: under *directed-only* PageRank it
    sat at position 127, because nothing imports an entry point. Both failure modes are pinned.
    """
    digest = build_repo_map(ROOT)
    for path in (
        "chimera/core/agent.py",
        "chimera/core/autonomous.py",
        "chimera/fusion/engine.py",
        "chimera/api/app.py",
    ):
        assert _rank_of(digest, path) >= 0, f"{path} did not survive the budget — ranking regressed"


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
