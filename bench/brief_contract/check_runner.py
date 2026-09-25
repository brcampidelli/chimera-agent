"""Grade one finished workspace: `python check_runner.py <workspace> <task_id>` prints one JSON line.

The workspace's `tally/` package is copied next to the fixture's ORIGINAL tests, so a test the agent
edited or added is neither run nor trusted. Two verdicts:

* ``tests_ok`` — the fixture's own tests still pass against the changed package;
* ``check_ok`` — the task's hidden check (``checks.py``) passes.

Success is both. Nothing here reads the agent's own report.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    ws, task_id = Path(sys.argv[1]), sys.argv[2]
    tmp = Path(tempfile.mkdtemp(prefix="h10-grade-"))
    out: dict[str, object] = {"task": task_id}
    try:
        if not (ws / "tally").is_dir():
            print(json.dumps({**out, "tests_ok": False, "check_ok": False, "detail": "no tally package"}))
            return
        shutil.copytree(ws / "tally", tmp / "tally", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(HERE / "fixture" / "tests", tmp / "tests", ignore=shutil.ignore_patterns("__pycache__"))
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
            cwd=tmp, capture_output=True, text=True, timeout=120,
            env={**__import__("os").environ, "PYTHONPATH": str(tmp), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        out["tests_ok"] = proc.returncode == 0
        out["tests_tail"] = proc.stdout.strip().splitlines()[-1:] if proc.stdout.strip() else []
        sys.path.insert(0, str(tmp))
        sys.path.insert(1, str(HERE))
        try:
            from checks import CHECKS  # noqa: PLC0415

            CHECKS[task_id]()
            out["check_ok"] = True
        except BaseException as exc:  # noqa: BLE001 — argparse's SystemExit included: any failure is a failed check
            out["check_ok"] = False
            out["detail"] = f"{type(exc).__name__}: {exc}"[:300]
            out["trace"] = traceback.format_exc()[-600:]
        print(json.dumps(out))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
