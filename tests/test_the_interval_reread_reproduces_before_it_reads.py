"""`bench/interval_reread` reproduces every published interval before it re-reads it (PROTOCOL §11).

The re-read's RESULTS rest on two facts this test pins: every one of the 56 published intervals is
first recomputed, with the method that printed it, to the published precision — and exactly two of
them cross their criterion under the closed-form interval that replaces it. If a reader, a results
file or a function in `chimera/eval/proportions.py` moves, the published corrections are stale and
this goes red.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_reread_reproduces_everything_and_crosses_where_its_results_say(tmp_path: Path) -> None:
    out = tmp_path / "reread.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "bench" / "interval_reread" / "reread.py"), "--json", str(out)],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=600, check=False,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    record = json.loads(out.read_text(encoding="utf-8"))
    assert len(record) == 56
    assert [r["verdict"] for r in record if not r["reproduced"]] == []
    crossed = sorted((r["bench"], r["verdict"]) for r in record if r["crosses_criterion"])
    assert crossed == [
        ("bench/learning_lift/results_recurring/learning.json", "/family_transfer/later_member significant=True"),
        ("harness_bench", "TERCILE × B checklist interaction (95%)"),
    ]
    committed = (ROOT / "bench" / "interval_reread" / "results" / "reread.json").read_text(encoding="utf-8")
    assert json.loads(committed) == record, "results/reread.json is stale: re-run the reader"
