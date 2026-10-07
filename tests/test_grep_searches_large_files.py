"""`grep` searches a large file instead of skipping it in silence.

Measured 2026-10-06 driving the desktop: the 1 MB cap skipped the desktop's 1.7 MB i18n.tsx with
no word said, so every search for keys that are there answered "no matches" and the agent spent
half an hour concluding they did not exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.tools import search
from chimera.tools.search import GrepTool


def test_a_match_near_the_end_of_a_large_file_is_found(tmp_path: Path) -> None:
    big = tmp_path / "i18n.tsx"
    filler = "x" * 99 + "\n"
    big.write_text(filler * 15_000 + 'const pt: Dict = {\n', encoding="utf-8")  # ~1.5 MB
    assert big.stat().st_size > search._MAX_FILE_BYTES
    out = GrepTool(tmp_path).run(pattern=r"const pt: Dict", path=".")
    assert "i18n.tsx:15001: const pt: Dict = {" in out


def test_a_file_too_large_to_read_is_named_not_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(search, "_MAX_STREAM_BYTES", 100)
    (tmp_path / "huge.log").write_text("needle\n" * 100, encoding="utf-8")
    (tmp_path / "small.txt").write_text("nothing here\n", encoding="utf-8")
    out = GrepTool(tmp_path).run(pattern="needle", path=".")
    assert out.startswith("no matches")
    assert "not searched: 1 file(s)" in out and "huge.log" in out
