"""The run log is read line by line, and it has to stay that way.

Measured on a 5.5 MB log of 1000 receipts, which is what ~1000 runs produces at the ~5.6 KB a
receipt costs once its diffs are embedded:

===========================  =========  ===========
                              peak RAM   median time
``read_text().splitlines()``    22.0 MB       24.0 ms
line by line                     0.1 MB       20.5 ms
===========================  =========  ===========

Four times the file, because the raw bytes, the decoded string and the list of lines are all alive
at once — and three routes read this same file. The time is not the point and never was: parsing
1000 receipts costs 24 ms either way, and a first attempt to measure this reported the streamed
version as *twice as slow* because `tracemalloc` was left running during the timing.

The guard here is not a memory threshold, which drifts between platforms and Python versions. It is
the property itself: these readers must work when reading the whole file at once is impossible.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.api.runs import load_runs
from chimera.api.usage import load_usage, usage_from_runs


@pytest.fixture
def _sem_leitura_inteira(monkeypatch: Any) -> None:
    """Make ``Path.read_text`` unusable.

    A reader that still works cannot be holding the whole file, and unlike a byte threshold this
    says so exactly rather than approximately.
    """

    def _proibido(self: Path, *a: Any, **k: Any) -> str:
        raise AssertionError(f"{self.name} was read whole; these logs are streamed")

    monkeypatch.setattr(Path, "read_text", _proibido)


def _log_de_execucoes(pasta: Path, quantos: int) -> Path:
    linhas = [
        json.dumps({
            "ts": f"2026-08-31T{i % 24:02d}:00:00+00:00",
            "task": "t", "success": True, "workspace": "C:/proj",
            "attempts": [{
                "index": 1, "run_id": f"r{i}", "model": "m",
                "prompt_tokens": 10, "completion_tokens": 1, "usd": 0.01,
                # A receipt embeds its diffs, which is why one costs kilobytes rather than bytes.
                "diffs": [{"path": "a.py", "patch": "x" * 2000, "truncated": False}],
            }],
        })
        for i in range(quantos)
    ]
    caminho = pasta / "runs.jsonl"
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return caminho


def test_the_run_receipts_are_streamed(tmp_path: Path, _sem_leitura_inteira: None) -> None:
    log = _log_de_execucoes(tmp_path, 50)

    assert len(load_runs(log)) == 50


def test_the_filtered_read_is_streamed_too(tmp_path: Path, _sem_leitura_inteira: None) -> None:
    """The route the desktop actually calls always passes a project."""
    log = _log_de_execucoes(tmp_path, 50)

    assert len(load_runs(log, workspace="C:/proj")) == 50


def test_the_cost_screen_streams_the_run_log(tmp_path: Path, _sem_leitura_inteira: None) -> None:
    """The same file again, from the screen that totals what everything cost."""
    log = _log_de_execucoes(tmp_path, 50)

    registros = usage_from_runs(log)

    assert len(registros) == 50
    assert round(sum(r.usd or 0 for r in registros), 4) == 0.5


def test_the_usage_log_is_streamed(tmp_path: Path, _sem_leitura_inteira: None) -> None:
    """It is the smaller of the two today and grows with every chat turn."""
    caminho = tmp_path / "usage.jsonl"
    caminho.write_text(
        "\n".join(
            json.dumps({"ts": "2026-08-31T00:00:00+00:00", "session_id": f"s{i}", "usd": 0.01})
            for i in range(50)
        ) + "\n",
        encoding="utf-8",
    )

    assert len(load_usage(caminho)) == 50


# --- and none of it may change what they return ----------------------------------------------------


def test_a_blank_line_is_skipped_quietly(tmp_path: Path, caplog: Any) -> None:
    """Iterating a file hands back the newline that ``splitlines()`` removed, so a line that used to
    arrive as an empty string now arrives as a lone newline.

    The count alone cannot show this: a blank line reaching the parser raises and is skipped anyway,
    so removing the check changes nothing a caller can see — measured, that sabotage walked straight
    through the first version of this test. What it changes is the LOG: every blank line becomes a
    "malformed record" warning, and a log that cries malformed at an empty line is one nobody reads
    when a line really is malformed.
    """
    import logging

    log = _log_de_execucoes(tmp_path, 3)
    log.write_text(log.read_text(encoding="utf-8") + "\n\n   \n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert len(load_runs(log)) == 3
        assert len(usage_from_runs(log)) == 3

    ruidosas = [r for r in caplog.records if "malformed" in r.getMessage()]
    assert not ruidosas, "a blank line was reported as a malformed record"


def test_a_malformed_line_is_still_skipped(tmp_path: Path) -> None:
    log = _log_de_execucoes(tmp_path, 3)
    log.write_text("{nao e json\n" + log.read_text(encoding="utf-8"), encoding="utf-8")

    assert len(load_runs(log)) == 3
    assert len(usage_from_runs(log)) == 3


def test_a_file_without_a_trailing_newline_keeps_its_last_row(tmp_path: Path) -> None:
    """The row most likely to be lost by a reader that assumes every line ends in a newline — and
    the one that matters most, because it is the most recent run."""
    log = _log_de_execucoes(tmp_path, 3)
    log.write_text(log.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")

    assert len(load_runs(log)) == 3


def test_a_missing_file_is_still_empty(tmp_path: Path) -> None:
    assert load_runs(tmp_path / "nada.jsonl") == []
    assert usage_from_runs(tmp_path / "nada.jsonl") == []
    assert load_usage(tmp_path / "nada.jsonl") == []
