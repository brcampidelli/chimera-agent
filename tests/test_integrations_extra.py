"""Tests for the v0.12 integrations: data-analysis skill, media download, local image backend.

Fakes only — no LLM, no yt-dlp, no torch/diffusers, no network.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.skills.builtin.data_skills import DataAnalysisSkill, DataVisualizationSkill
from chimera.tools import download, media


class _FakeBackend:
    def __init__(self, content: str) -> None:
        self.content = content

    def complete(self, messages: Any, *, model: Any = None, temperature: float = 0.1, **k: Any) -> Any:
        return SimpleNamespace(content=self.content, model="fake", prompt_tokens=0, completion_tokens=0)


# --- data-analysis skill -----------------------------------------------------------------


def test_data_analysis_skill_emits_code() -> None:
    skill = DataAnalysisSkill(backend=_FakeBackend("import pandas as pd\nprint('done')"))
    res = skill.run(task="predict churn from customers.csv")
    assert res.ok and "pandas" in res.output


def test_data_analysis_skill_requires_task() -> None:
    assert not DataAnalysisSkill(backend=_FakeBackend("x")).run().ok


def test_data_analysis_skill_is_registered() -> None:
    from chimera.skills.builtin import register_builtin_skills
    from chimera.skills.registry import SkillRegistry

    reg = SkillRegistry()
    register_builtin_skills(reg, backend=_FakeBackend("x"))
    assert "data_analysis" in reg.names()
    assert "data_visualization" in reg.names()


# --- data-visualization skill ------------------------------------------------------------


def test_data_visualization_skill_emits_code() -> None:
    skill = DataVisualizationSkill(backend=_FakeBackend("import matplotlib\nmatplotlib.use('Agg')"))
    res = skill.run(task="bar chart of sales by month", dataset="sales.csv", out="sales.png")
    assert res.ok and "matplotlib" in res.output


def test_data_visualization_skill_requires_task() -> None:
    assert not DataVisualizationSkill(backend=_FakeBackend("x")).run().ok


# --- media download (yt-dlp) -------------------------------------------------------------


def _offline_ssrf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Map any hostname to a public IP so the SSRF guard passes without real DNS."""
    import chimera.scrape.ssrf as ssrf

    monkeypatch.setattr(ssrf, "_resolve_ips", lambda host: ["93.184.216.34"])


def test_download_media_saves_and_reports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_dl(url: str, outtmpl: str, audio_only: bool) -> dict[str, Any]:
        Path(outtmpl.replace("%(title).80s.%(ext)s", "Clip.mp4")).write_bytes(b"video")
        return {"title": "Clip", "ext": "mp4"}

    _offline_ssrf(monkeypatch)
    monkeypatch.setattr(download, "_ytdlp_download", fake_dl)
    out = download.DownloadMediaTool(workspace=tmp_path).run(url="https://youtube.com/1")
    assert "downloaded 'Clip'" in out and "Clip.mp4" in out


def test_download_media_missing_extra(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _offline_ssrf(monkeypatch)
    monkeypatch.setattr(download, "_ytdlp_download", lambda u, o, a: None)
    out = download.DownloadMediaTool(workspace=tmp_path).run(url="https://youtube.com/1")
    assert out.startswith("error:") and "media-dl" in out


def test_download_media_needs_url(tmp_path: Path) -> None:
    assert download.DownloadMediaTool(workspace=tmp_path).run().startswith("error:")


def test_download_media_is_a_governed_fetch_tool() -> None:
    # It pulls untrusted content from the internet -> the taint/fence envelope must cover it.
    from chimera.governance.ledger import FETCH_TOOLS

    assert "download_media" in FETCH_TOOLS


# --- local image backend (diffusers) -----------------------------------------------------


def test_image_local_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_local(prompt: str, out: Path, model: str, size: str) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    monkeypatch.setattr(media, "_generate_local", fake_local)
    monkeypatch.setattr(media, "get_settings", lambda: SimpleNamespace(
        image_backend="local", image_model_local="flux", key_pool=lambda p: []))
    target = tmp_path / "img.png"
    res = media.ImageGenTool().run(prompt="a cat", out=str(target))
    assert "saved image" in res and "local: flux" in res and target.exists()


def test_image_local_missing_extra(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def raise_import(prompt: str, out: Path, model: str, size: str) -> None:
        raise ImportError("no diffusers")

    monkeypatch.setattr(media, "_generate_local", raise_import)
    monkeypatch.setattr(media, "get_settings", lambda: SimpleNamespace(
        image_backend="local", image_model_local="flux", key_pool=lambda p: []))
    res = media.ImageGenTool().run(prompt="x", out=str(tmp_path / "i.png"))
    assert res.startswith("error:") and "imagegen-local" in res
