"""`bench/interval_reread` reproduces every published interval before it re-reads it (PROTOCOL §11).

The re-read's RESULTS rest on two facts this test pins: every one of the 56 published intervals is
first recomputed, with the method that printed it, to the published precision — and exactly two of
them cross their criterion under the closed-form interval that replaces it. If a reader, a results
file or a function in `chimera/eval/proportions.py` moves, the published corrections are stale and
this goes red.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_the_reread_reproduces_everything_and_crosses_where_its_results_say(tmp_path: Path) -> None:
    # The re-read reads committed results files. A copy of the tree made without them (the WSL gate
    # rsyncs with `--exclude 'bench/*/results*'`) cannot run it, and says so instead of failing.
    if not (ROOT / "bench" / "harness_bench" / "results" / "2026-09-13-factorial.jsonl").is_file():
        pytest.skip("bench results are not in this checkout; the re-read reads them")
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


def _load_reader() -> Any:
    spec = importlib.util.spec_from_file_location("interval_reread_reader", ROOT / "bench" / "interval_reread" / "reread.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_section_b_reads_its_frozen_inputs_not_whatever_the_tree_holds_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A bench committed after the re-read writes `PairedResult.summary()` with a Bonett-Price
    # interval. If section B searched the live tree it would pick that file up, fail to reproduce it
    # with the conditional method, and turn this re-read red for a commit that never touched it. And
    # a search needs a git checkout: a `git archive` copy has none. So the copy here has no `.git`,
    # and carries one such newer file beside the ten registered ones.
    reader = _load_reader()
    if not all((ROOT / name).is_file() for name in reader.PAIRED_SUMMARY_FILES):
        pytest.skip("bench results are not in this checkout; the re-read reads them")
    for name in reader.PAIRED_SUMMARY_FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    newer = tmp_path / "bench" / "a_later_bench" / "results" / "paired.json"
    newer.parent.mkdir(parents=True)
    newer.write_text(json.dumps({"n": 20, "delta": 0.2, "discordant": {"baseline_only": 0, "treatment_only": 4},
                                 "diff_ci": [-0.01, 0.38], "significant": False}), encoding="utf-8")
    assert not (tmp_path / ".git").exists()

    assert len(reader.PAIRED_SUMMARY_FILES) == 10
    summaries = reader.paired_summaries(tmp_path)
    assert len(summaries) == 22  # the count PREREGISTRATION.md section B names
    assert all(name != "bench/a_later_bench/results/paired.json" for name, _, _ in summaries)

    monkeypatch.setattr(reader, "ROOT", tmp_path)
    reader.record.clear()
    reader.reread_paired_summaries()
    capsys.readouterr()
    assert len(reader.record) == 22
    assert all(r["reproduced"] for r in reader.record)
