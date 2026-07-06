"""Tests for read_document (M13 A3) — MarkItDown wrapper with a graceful missing-extra path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.tools import documents
from chimera.tools.documents import ReadDocumentTool


def _doc(tmp_path: Path, name: str = "report.docx") -> ReadDocumentTool:
    (tmp_path / name).write_bytes(b"binary-ish content")  # existence is what the tool checks
    return ReadDocumentTool(tmp_path)


def test_missing_extra_gives_install_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_path: str) -> str:
        raise ImportError("No module named 'markitdown'")

    monkeypatch.setattr(documents, "_markitdown_convert", _raise)
    out = _doc(tmp_path).run(path="report.docx")
    assert "chimera-agent[documents]" in out and out.startswith("error:")


def test_converts_and_returns_markdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(documents, "_markitdown_convert", lambda _p: "# Title\n\nBody text.")
    out = _doc(tmp_path).run(path="report.docx")
    assert out == "# Title\n\nBody text."


def test_truncates_large_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(documents, "_markitdown_convert", lambda _p: "x" * 25_000)
    out = _doc(tmp_path).run(path="report.docx")
    assert "truncated, 25000 chars total" in out
    assert len(out) < 25_000


def test_missing_file(tmp_path: Path) -> None:
    out = ReadDocumentTool(tmp_path).run(path="ghost.pdf")
    assert out.startswith("error: file not found")


def test_conversion_failure_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_path: str) -> str:
        raise ValueError("unsupported format")

    monkeypatch.setattr(documents, "_markitdown_convert", _boom)
    out = _doc(tmp_path).run(path="report.docx")
    assert out.startswith("error: could not read") and "unsupported format" in out


def test_registered_and_classified_as_read_tool() -> None:
    from chimera.governance.ledger import READ_TOOLS
    from chimera.tools import default_registry

    assert "read_document" in default_registry().names()
    assert "read_document" in READ_TOOLS  # capability ledger sees it as a read, not a write


def test_path_escape_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(documents, "_markitdown_convert", lambda _p: "ok")
    with pytest.raises(Exception):  # noqa: B017 — resolve_in_workspace rejects escaping paths
        _run_escape(tmp_path)


def _run_escape(tmp_path: Path) -> Any:
    return ReadDocumentTool(tmp_path).run(path="../../etc/passwd")
