"""The mutation gate runs the tests that exist for its modules — not the ones that existed in July.

mutmut maps each selected test to the functions it calls and, per mutant, re-runs only those. A test
file missing from ``[tool.mutmut].pytest_add_cli_args_test_selection`` therefore does not exist for
the gate, however well it tests the module. The list was written in 2026-07 for the five original
modules; the modules grew new functions tested in NEW files; and the weekly run of 2026-10-05
reported 449 survivors, most of them killed by tests the gate was never shown (the confirm gate's
`ask=` path, for one, is tested by `test_the_tui_can_answer_its_own_question.py`).

This makes the list grow with the tests: any test file that names an in-scope module must be in the
selection or in :data:`EXCLUDED` below with the reason it cannot be. So must a test that loads a
``bench/`` runner which names one: ``tests/test_run_paired.py`` drives
``chimera.eval.paired.run_paired_experiment`` through ``bench/local_lift/run_paired.py`` without
ever spelling the module, and the first version of this check let it fall out of the selection
unnoticed.
"""

from __future__ import annotations

import ast
import re
import tomllib
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Test files that name an in-scope module and are deliberately NOT run by the mutation gate. Each
#: one is slow enough that, multiplied by every mutant of the functions it touches, it would push the
#: weekly job past its timeout; what they cover of the in-scope modules is covered by faster files.
EXCLUDED: dict[str, str] = {
    "tests/test_api.py": "~65s alone; every runs.py entry point it reaches is tested in test_runs.py",
    "tests/test_a_pull_request_is_asked_every_time.py": "~100s; spawns real git/gh subprocesses",
    "tests/test_memory_graph_bench.py": "~45s bench run; uses paired.py only as a consumer",
    "tests/test_a_spec_test_is_evidence_only_if_it_could_have_failed.py": "~25s of real test runs",
    "tests/test_background_jobs_stay_inside_the_fences_run_shell_has.py": "~25s of real processes",
    "tests/test_pr_watch_reads_and_never_acts.py": "~20s; drives the scheduler end to end",
    "tests/test_a_conversation_says_what_it_needs_from_facts.py": "~13s of whole-session runs",
    # Consumers through a bench runner (see _bench_files_it_loads):
    "tests/test_a_decision_has_a_contract.py": (
        "reads only the JUDGE_SYSTEM string from bench/governance_judge/run.py; the policy.py names "
        "that runner imports are never called by it"
    ),
    "tests/test_the_gateway_tells_its_ledger_whose_turn_it_is.py": (
        "~9s of gateway turns; the same bench runner's ledger/audit paths are driven by "
        "test_the_terminal_registry_is_the_one_chat_builds.py, which is selected"
    ),
}


def _mutmut() -> dict[str, list[str]]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    cfg = data["tool"]["mutmut"]
    return {"only_mutate": list(cfg["only_mutate"]),
            "selection": list(cfg["pytest_add_cli_args_test_selection"])}


def _names_module(text: str, module_path: str) -> bool:
    dotted = module_path.removesuffix(".py").replace("/", ".")
    package, name = dotted.rsplit(".", 1)
    return bool(
        re.search(re.escape(dotted) + r"\b", text)
        or re.search(r"from\s+" + re.escape(package) + r"\s+import\s+[^\n]*\b" + name + r"\b", text)
    )


def _bench_files_it_loads(text: str, root: Path = ROOT) -> set[Path]:
    """The ``bench/`` files a test imports or loads by path.

    Three spellings exist in this suite: a path handed to ``spec_from_file_location`` (written either
    as ``"bench/d/f.py"`` or as ``"bench" / "d" / "f.py"``), ``from bench.d.f import ...``, and a
    ``sys.path`` entry into ``bench/d`` followed by a bare ``import f``. A runner reached any of these
    ways runs the in-scope code it imports under the test, exactly as a direct import would.
    """
    with warnings.catch_warnings():  # another file's invalid escape is not this test's business
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(text)
    strings = sorted(
        (n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)),
        key=lambda n: (n.lineno, n.col_offset),
    )
    consts = [n.value for n in strings]
    # Source order matters: `"bench" / "local_lift" / "run_paired.py"` is three adjacent constants.
    joined = "/".join(consts)
    bench = root / "bench"
    found: set[Path] = set()
    if "spec_from_file_location" in text or "import_module" in text:
        for candidate in bench.rglob("*.py"):
            if "bench/" + candidate.relative_to(bench).as_posix() in joined:
                found.add(candidate)
    dirs = [c.split("bench/", 1)[1].strip("/") for c in consts if c.startswith("bench/")]
    dirs += [consts[i + 1] for i, c in enumerate(consts[:-1]) if c == "bench"]
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    for module in modules:
        if module.startswith("bench."):
            dotted = root / (module.replace(".", "/") + ".py")
            if dotted.is_file():
                found.add(dotted)
        for d in dirs:
            bare = bench / d / (module.split(".")[0] + ".py")
            if bare.is_file():
                found.add(bare)
    return found


def _in_scope_hits(text: str, only_mutate: list[str], root: Path = ROOT) -> list[str]:
    hits = [m for m in only_mutate if _names_module(text, m)]
    for runner in sorted(_bench_files_it_loads(text, root)):
        source = runner.read_text(encoding="utf-8", errors="replace")
        via = runner.relative_to(root).as_posix()
        hits += [f"{m} via {via}" for m in only_mutate if _names_module(source, m) and m not in hits]
    return hits


def test_every_test_of_an_in_scope_module_is_selected_or_excused() -> None:
    cfg = _mutmut()
    selected = {s for s in cfg["selection"] if not s.startswith("-")}
    missing: list[str] = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        rel = path.relative_to(ROOT).as_posix()
        # This file spells module names and runner paths as test DATA; it runs none of them.
        if rel in selected or rel in EXCLUDED or path.resolve() == Path(__file__).resolve():
            continue
        hits = _in_scope_hits(path.read_text(encoding="utf-8"), cfg["only_mutate"])
        if hits:
            missing.append(f"{rel} (names {', '.join(hits)})")
    assert not missing, (
        "These test files name a module the mutation gate mutates, or load a bench runner that "
        "does, but the gate never runs "
        "them — so every mutant only they would kill is reported as a survivor. Add them to "
        "[tool.mutmut].pytest_add_cli_args_test_selection in pyproject.toml, or to EXCLUDED here "
        "with the reason:\n  " + "\n  ".join(missing)
    )


def test_the_selection_and_the_exclusions_name_files_that_exist() -> None:
    cfg = _mutmut()
    for entry in cfg["selection"]:
        target = entry.split("=", 1)[1] if entry.startswith("--deselect=") else entry
        assert (ROOT / target.split("::", 1)[0]).is_file(), entry
    for rel in EXCLUDED:
        assert (ROOT / rel).is_file(), f"stale exclusion: {rel}"
    # Excluded and selected at once is a contradiction the gate would silently resolve one way.
    assert not set(EXCLUDED) & set(cfg["selection"])


def test_a_deselected_test_still_exists() -> None:
    # A --deselect naming a test that was renamed deselects nothing, silently; pytest does not warn.
    for entry in _mutmut()["selection"]:
        if not entry.startswith("--deselect="):
            continue
        file_part, test_name = entry.split("=", 1)[1].split("::", 1)
        text = (ROOT / file_part).read_text(encoding="utf-8")
        assert re.search(rf"^def {re.escape(test_name)}\(", text, re.M), entry


def test_no_selected_test_changes_directory_unless_it_is_deselected() -> None:
    # mutmut 3.6 records which functions a test reached by resolving `source_paths` against the
    # CURRENT directory; a test that chdirs makes every later hit raise, and the stats run aborts with
    # "failed to collect stats" — the whole gate, not one test. Such tests are deselected by name.
    import ast
    import warnings

    cfg = _mutmut()
    deselected = {e.split("=", 1)[1] for e in cfg["selection"] if e.startswith("--deselect=")}
    offenders: list[str] = []
    for entry in cfg["selection"]:
        if entry.startswith("-"):
            continue
        with warnings.catch_warnings():  # another file's invalid escape is not this test's business
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse((ROOT / entry).read_text(encoding="utf-8"))
        for node in tree.body:
            if (
                isinstance(node, ast.FunctionDef)
                and node.name.startswith("test")
                and "chdir" in ast.unparse(node)
                and f"{entry}::{node.name}" not in deselected
            ):
                offenders.append(f"{entry}::{node.name}")
    assert not offenders, "deselect these in [tool.mutmut] or drop the chdir:\n  " + "\n  ".join(offenders)


def test_a_test_that_reaches_an_in_scope_module_only_through_a_bench_runner_is_seen(tmp_path: Path) -> None:
    """Every way a test in this suite loads a runner, each in a text that never names the module."""
    runner = tmp_path / "bench" / "lift" / "run.py"
    runner.parent.mkdir(parents=True)
    runner.write_text("from chimera.eval.paired import compare_paired\n", encoding="utf-8")
    only = ["chimera/eval/paired.py"]
    by_parts = (
        "import importlib.util\n"
        "P = ROOT / 'bench' / 'lift' / 'run.py'\n"
        "spec = importlib.util.spec_from_file_location('r', P)\n"
    )
    by_string = (
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('r', R / 'bench/lift/run.py')\n"
    )
    by_dotted = "from bench.lift.run import compare_paired\n"
    by_sys_path = "import sys\nsys.path.insert(0, str(R / 'bench/lift'))\nimport run\n"
    for text in (by_parts, by_string, by_dotted, by_sys_path):
        assert _in_scope_hits(text, only, tmp_path) == ["chimera/eval/paired.py via bench/lift/run.py"], text
    # Naming the file as data (a path in a list, nothing loaded) does not run it.
    assert _in_scope_hits("PATHS = ['bench/lift/run.py']\n", only, tmp_path) == []
