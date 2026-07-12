"""Tests for the media download tool — the SSRF guard runs before yt-dlp is ever invoked."""

from __future__ import annotations

from pathlib import Path

from chimera.tools import download as download_mod
from chimera.tools.download import DownloadMediaTool


def test_needs_a_url(tmp_path: Path) -> None:
    assert DownloadMediaTool(tmp_path).run(url="").startswith("error:")


def test_blocks_ssrf_url_before_downloading(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A private/metadata URL must be rejected up front — yt-dlp must never be called.
    called: list[str] = []
    monkeypatch.setattr(download_mod, "_ytdlp_download", lambda *a, **k: called.append("hit") or {})
    out = DownloadMediaTool(tmp_path).run(url="http://169.254.169.254/latest/meta-data/")
    assert out.startswith("error:") and "blocked" in out
    assert called == []  # download was never attempted


def test_blocks_non_http_scheme(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(download_mod, "_ytdlp_download", lambda *a, **k: {"title": "x"})
    assert DownloadMediaTool(tmp_path).run(url="file:///etc/passwd").startswith("error:")
