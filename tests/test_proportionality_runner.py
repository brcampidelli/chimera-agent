"""Offline regressions for the P1-17 harness; no generated code or model calls."""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BENCH = Path(__file__).resolve().parents[1] / "bench" / "proportionality"

def _load(name):
    spec = importlib.util.spec_from_file_location(f"proportionality_{name}", BENCH / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

report = _load("report")
run = _load("run")


class Regressions(unittest.TestCase):
    def test_partial_and_empty_reports_are_inconclusive(self):
        row = {"family": "read", "id": "r01", "rep": 0, "arm": "off", "error": "",
               "usd": 0, "ok": True, "steps": 1, "completion_tokens": 1, "files_changed": 0}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.jsonl"
            for rows in ([], [row], [row, {**row, "arm": "on"}]):
                path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    report.main(str(path))
                self.assertIn("INCONCLUSIVE", output.getvalue())
                self.assertIn("missing controls", output.getvalue())

    def test_imports_ignore_other_bench_modules_and_scripts_run_directly(self):
        from types import ModuleType

        with patch.dict(sys.modules, {"corpus": ModuleType("corpus"), "tasks": ModuleType("tasks")}):
            pairs = run._pairs(1)
            self.assertEqual(sum(pair["family"] == "control" for pair in pairs), 2)
            with tempfile.TemporaryDirectory() as tmp:
                rows = Path(tmp) / "runs.jsonl"
                rows.write_text("", encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    report.main(str(rows))
                self.assertIn("missing controls", output.getvalue())
                report_cli = subprocess.run([sys.executable, str(BENCH / "report.py"), str(rows)],
                                            cwd=tmp, capture_output=True, text=True, check=False)
                self.assertEqual(report_cli.returncode, 0, report_cli.stderr)
                self.assertIn("INCONCLUSIVE", report_cli.stdout)
                run_cli = subprocess.run([sys.executable, str(BENCH / "run.py"), "--help"],
                                         cwd=tmp, capture_output=True, text=True, check=False)
                self.assertEqual(run_cli.returncode, 0, run_cli.stderr)

    def test_each_run_gets_fresh_home_and_restores_environment(self):
        homes = []
        def fake(pair, arm, out, remaining):
            homes.append(os.environ["CHIMERA_HOME"])
            self.assertTrue(Path(homes[-1]).is_dir())
            self.assertEqual(remaining, 0.25)
            return {}
        before = os.environ.get("CHIMERA_HOME")
        with patch.object(run, "_run_one_inner", side_effect=fake):
            run._run_one({}, "off", Path("."), 0.25)
            run._run_one({}, "on", Path("."), 0.25)
        self.assertNotEqual(*homes)
        self.assertEqual(os.environ.get("CHIMERA_HOME"), before)
        self.assertTrue(all(not Path(home).exists() for home in homes))

    def test_edited_module_collection_errors_are_failed_edits(self):
        from chimera.sandbox.base import SandboxResult
        from chimera.sandbox.docker import DockerSandbox

        item = {"test": "tests/test_it.py", "files": {"tests/test_it.py": ""}}
        with tempfile.TemporaryDirectory() as tmp:
            for error in ("ImportError", "ModuleNotFoundError", "SyntaxError"):
                result = SandboxResult(exit_code=2, stdout=f"ERROR collecting tests/test_it.py\n{error}")
                with patch.object(DockerSandbox, "run", return_value=result):
                    verdict = run._verdict({"family": "edit", "item": item}, Path(tmp), "", {})
                self.assertFalse(verdict["ok"])
            with patch.object(DockerSandbox, "run", return_value=SandboxResult(exit_code=2, stderr="internal error")), self.assertRaises(RuntimeError):
                run._verdict({"family": "edit", "item": item}, Path(tmp), "", {})

    def test_control_never_reads_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(run, "_snapshot", return_value={}):
            verdict = run._verdict({"family": "control", "item": {"kind": "build"}}, Path(tmp), "", {})
            self.assertFalse(verdict["ok"])

    def test_started_pair_uses_absolute_ceiling_for_second_arm(self):
        from types import SimpleNamespace

        pair = {"family": "read", "id": "r01", "rep": 0, "first": "off"}
        calls = []
        def fake_run(pair, arm, out, remaining):
            calls.append((arm, remaining))
            return {"family": "read", "id": "r01", "rep": 0, "arm": arm, "usd": 4.49 if arm == "off" else .10,
                    "cost_known": True, "error": "", "ok": True, "steps": 1, "completion_tokens": 1}
        grader = SimpleNamespace(available=lambda: True, run=lambda *a, **kw: SimpleNamespace(exit_code=0, timed_out=False))
        with tempfile.TemporaryDirectory() as tmp, patch.object(sys, "argv", ["run", "--out", tmp]), \
             patch.object(run, "_grader", return_value=grader), \
             patch.object(run.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
             patch.object(run, "_record_switches"), patch.object(run, "_pairs", return_value=[pair]), \
             patch.object(run, "_run_one", side_effect=fake_run):
            self.assertEqual(run.main(), 0)
            self.assertEqual([x[0] for x in calls], ["off", "on"])
            self.assertAlmostEqual(calls[0][1], 4.75)
            self.assertAlmostEqual(calls[1][1], .51)
            self.assertFalse((Path(tmp) / "inflight.json").exists())

    def test_four_pair_projection_keeps_shuffled_prefix(self):
        from types import SimpleNamespace

        pairs = [{"family": "read", "id": f"r{i}", "rep": 0, "first": "off"} for i in range(10)]
        calls = []
        def fake_run(pair, arm, out, remaining):
            calls.append((pair["id"], arm))
            return {"family": "read", "id": pair["id"], "rep": 0, "arm": arm, "usd": .4,
                    "cost_known": True, "error": "", "ok": True, "steps": 1, "completion_tokens": 1}
        grader = SimpleNamespace(available=lambda: True, run=lambda *a, **kw: SimpleNamespace(exit_code=0, timed_out=False))
        with tempfile.TemporaryDirectory() as tmp, patch.object(sys, "argv", ["run", "--out", tmp]), \
             patch.object(run, "_grader", return_value=grader), \
             patch.object(run.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
             patch.object(run, "_record_switches"), patch.object(run, "_pairs", return_value=pairs), \
             patch.object(run, "_run_one", side_effect=fake_run):
            self.assertEqual(run.main(), 0)
            self.assertEqual(calls, [(f"r{i}", arm) for i in range(6) for arm in ("off", "on")])

    def test_interrupted_run_blocks_resume_before_grader_check(self):
        from types import SimpleNamespace

        pair = {"family": "read", "id": "r01", "rep": 0, "first": "off"}
        grader = SimpleNamespace(available=lambda: True, run=lambda *a, **kw: SimpleNamespace(exit_code=0, timed_out=False))
        with tempfile.TemporaryDirectory() as tmp, patch.object(sys, "argv", ["run", "--out", tmp]), \
             patch.object(run, "_grader", return_value=grader), \
             patch.object(run.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
             patch.object(run, "_record_switches"), patch.object(run, "_pairs", return_value=[pair]), \
             patch.object(run, "_run_one", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                run.main()
            self.assertTrue((Path(tmp) / "inflight.json").exists())
            with self.assertRaisesRegex(SystemExit, "in-flight cost"):
                run.main()

    def test_unknown_cost_and_excess_admission_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "runs.jsonl").write_text('{"usd": null, "error": "failed"}\n', encoding="utf-8")
            with patch.object(sys, "argv", ["run", "--out", tmp]), self.assertRaisesRegex(SystemExit, "unknown"):
                run.main()
            with patch.object(sys, "argv", ["run", "--out", tmp, "--stop-usd", "5"]), self.assertRaises(SystemExit):
                run.main()

    def test_timeout_cost_is_unknown_and_spend_stop_is_incomplete(self):
        from types import SimpleNamespace

        budget = SimpleNamespace(estimated=False, spent=.04)
        for amount in (0.0, .03):
            result = SimpleNamespace(stopped_reason="provider_timeout", usd=amount)
            cost, error = run._result_cost_and_error(result, budget)
            self.assertIsNone(cost)
            self.assertIn("timeout", error)
        result = SimpleNamespace(stopped_reason="spend", usd=.03)
        cost, error = run._result_cost_and_error(result, budget)
        self.assertEqual(cost, .04)
        self.assertIn("before completion", error)
        rows = [{"family": "read", "id": "r01", "rep": 0, "arm": arm, "error": error if arm == "on" else ""}
                for arm in ("off", "on")]
        self.assertFalse(report._pairs(rows, "read"))

    def test_timeout_stops_before_second_arm_and_blocks_resume(self):
        from types import SimpleNamespace

        pair = {"family": "read", "id": "r01", "rep": 0, "first": "off"}
        grader = SimpleNamespace(available=lambda: True, run=lambda *a, **kw: SimpleNamespace(exit_code=0, timed_out=False))
        cost, error = run._result_cost_and_error(SimpleNamespace(stopped_reason="provider_timeout", usd=0),
                                                  SimpleNamespace(estimated=False, spent=0))
        row = {"family": "read", "id": "r01", "rep": 0, "arm": "off", "usd": cost,
               "cost_known": False, "error": error, "ok": False, "steps": 1, "completion_tokens": 0}
        with tempfile.TemporaryDirectory() as tmp, patch.object(sys, "argv", ["run", "--out", tmp]), \
             patch.object(run, "_grader", return_value=grader), \
             patch.object(run.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
             patch.object(run, "_record_switches"), patch.object(run, "_pairs", return_value=[pair]), \
             patch.object(run, "_run_one", return_value=row) as run_one:
            self.assertEqual(run.main(), 1)
            self.assertEqual(run_one.call_count, 1)
            self.assertIsNone(json.loads((Path(tmp) / "runs.jsonl").read_text(encoding="utf-8"))["usd"])
            self.assertTrue((Path(tmp) / "inflight.json").exists())
            with self.assertRaisesRegex(SystemExit, "in-flight cost"):
                run.main()

    def test_output_lock_refuses_concurrent_runner(self):
        from chimera.core.filelock import try_hold

        with tempfile.TemporaryDirectory() as tmp:
            lock = try_hold(Path(tmp) / "runner")
            self.assertIsNotNone(lock)
            self.assertTrue(lock.real)
            try:
                with patch.object(sys, "argv", ["run", "--out", tmp]), \
                     patch.object(run, "_grader") as grader, \
                     self.assertRaisesRegex(SystemExit, "locked"):
                    run.main()
                grader.assert_not_called()
            finally:
                lock.release()

    def test_transport_failure_blocks_gateway_and_agent_retries(self):
        from types import SimpleNamespace

        attempts = []
        def transport(*args, **kwargs):
            attempts.append(kwargs)
            raise TimeoutError("transport interrupted")
        fake_litellm = SimpleNamespace(completion=transport)
        class Gateway:
            def complete(self):
                try:
                    return fake_litellm.completion(model="fake")
                except Exception:
                    return fake_litellm.completion(model="fake")
        backend = run._SingleAttemptBackend(Gateway())
        with patch.dict(sys.modules, {"litellm": fake_litellm}), run._single_attempt_transport():
            with self.assertRaises(run._AmbiguousCall):
                backend.complete()
            with self.assertRaises(run._AmbiguousCall):
                backend.complete()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["num_retries"], 0)
        self.assertTrue(backend.ambiguous)

    def test_grader_unavailable_never_becomes_a_pass(self):
        from chimera.sandbox.base import SandboxResult
        from chimera.sandbox.docker import DockerSandbox

        item = {"test": "tests/test_it.py", "files": {"tests/test_it.py": ""}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            DockerSandbox, "run", return_value=SandboxResult(exit_code=125)
        ) as invoke:
            with self.assertRaises(RuntimeError):
                run._verdict({"family": "edit", "item": item}, Path(tmp), "", {})
            self.assertEqual(invoke.call_args.kwargs["timeout"], run.GRADER_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
