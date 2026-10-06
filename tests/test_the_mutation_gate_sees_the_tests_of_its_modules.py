"""The mutation gate runs the tests that exist for its modules — not the ones that existed in July.

mutmut maps each selected test to the functions it calls and, per mutant, re-runs only those. A test
file missing from ``[tool.mutmut].pytest_add_cli_args_test_selection`` therefore does not exist for
the gate, however well it tests the module. The list was written in 2026-07 for the five original
modules; the modules grew new functions tested in NEW files; and the weekly run of 2026-10-05
reported 449 survivors, most of them killed by tests the gate was never shown (the confirm gate's
`ask=` path, for one, is tested by `test_the_tui_can_answer_its_own_question.py`).

This makes the list grow with the tests: any test file that names an in-scope module must be in the
selection or in :data:`EXCLUDED` below with the reason it cannot be.
"""

from __future__ import annotations

import re
import tomllib
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


def test_every_test_of_an_in_scope_module_is_selected_or_excused() -> None:
    cfg = _mutmut()
    selected = {s for s in cfg["selection"] if not s.startswith("-")}
    missing: list[str] = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in selected or rel in EXCLUDED:
            continue
        text = path.read_text(encoding="utf-8")
        hits = [m for m in cfg["only_mutate"] if _names_module(text, m)]
        if hits:
            missing.append(f"{rel} (names {', '.join(hits)})")
    assert not missing, (
        "These test files exercise a module the mutation gate mutates, but the gate never runs "
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
