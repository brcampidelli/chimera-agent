"""A shallow checkout can inspect results and run offline tests without the historical blob."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path


def test_offline_paths_work_without_history_and_paid_entries_fail_closed(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    checkout = tmp_path / "shallow"
    bench = checkout / "bench"
    bench.mkdir(parents=True)
    shutil.copytree(root / "bench" / "design_refine", bench / "design_refine",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(root / "bench" / "_legacy_defaults.py", bench / "_legacy_defaults.py")
    tests = checkout / "tests"
    tests.mkdir()
    shutil.copy2(root / "tests" / "test_design_refine_budget.py", tests / "test_design_refine_budget.py")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    report = subprocess.run([sys.executable, str(bench / "design_refine" / "run.py"), "--report"],
                            cwd=checkout, env=env, capture_output=True, text=True, timeout=60, check=False)
    assert report.returncode == 0, report.stderr
    assert "VERDICT: not adopted" in report.stdout

    # This is the existing budget test, run against the copied runner in a directory with no .git.
    budget = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                             str(tests / "test_design_refine_budget.py")],
                            cwd=checkout, env=env, capture_output=True, text=True, timeout=90, check=False)
    assert budget.returncode == 0, budget.stdout + budget.stderr

    # Fakes make the test harmless even if a preflight regresses; no backend may be reached.
    probe = textwrap.dedent("""
        from types import SimpleNamespace
        from unittest.mock import patch
        from bench.design_refine import run

        def refused(action):
            try:
                action()
            except SystemExit as exc:
                assert "registered baseline unavailable" in str(exc)
            else:
                raise AssertionError("a paid entry accepted a missing baseline")

        with patch.object(run, "ThreadPoolExecutor", side_effect=AssertionError("workers started")), \
             patch.object(run.propose, "propose", side_effect=AssertionError("backend reached")), \
             patch.object(run, "load_design", side_effect=AssertionError("design loaded")):
            for phase in (run.cmd_gen, run.cmd_corpus, run.cmd_refine):
                refused(lambda: phase("ds"))
            refused(lambda: run.generate(None, None, "fixture", "B"))
            refused(lambda: run.run_refine_item("ds", 0, 0))

        # A present but different blob is refused before workers as well.
        with patch.object(run.subprocess, "run", return_value=SimpleNamespace(stdout=b"wrong bytes")), \
             patch.object(run, "ThreadPoolExecutor", side_effect=AssertionError("workers started")):
            try:
                run.cmd_gen("ds")
            except SystemExit as exc:
                assert "not the registered one" in str(exc)
            else:
                raise AssertionError("a mismatched baseline was accepted")
    """)
    check = subprocess.run([sys.executable, "-c", probe], cwd=checkout, env=env,
                           capture_output=True, text=True, timeout=60, check=False)
    assert check.returncode == 0, check.stdout + check.stderr
