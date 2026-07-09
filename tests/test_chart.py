"""Tests for the render_chart Vega-Lite tool. Fakes only — HTML path is dep-free; static is monkeypatched."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.tools import chart

_SPEC = {
    "data": {"values": [{"a": "A", "b": 5}, {"a": "B", "b": 8}]},
    "mark": "bar",
    "encoding": {"x": {"field": "a", "type": "nominal"}, "y": {"field": "b", "type": "quantitative"}},
}


def test_html_is_dep_free_and_embeds_spec(tmp_path: Path) -> None:
    out = tmp_path / "c.html"
    res = chart.RenderChartTool(workspace=tmp_path).run(spec=_SPEC, out=str(out))
    assert "saved html chart" in res and out.exists()
    body = out.read_text(encoding="utf-8")
    assert "vegaEmbed" in body and "cdn.jsdelivr.net/npm/vega-lite@5" in body
    assert '"mark": "bar"' in body  # the spec is inlined verbatim (inert data, not code)


def test_accepts_json_string_spec(tmp_path: Path) -> None:
    res = chart.RenderChartTool(workspace=tmp_path).run(spec=json.dumps(_SPEC))
    assert "saved html chart" in res


def test_rejects_non_spec(tmp_path: Path) -> None:
    tool = chart.RenderChartTool(workspace=tmp_path)
    assert tool.run(spec=123).startswith("error:")
    assert tool.run(spec="not json").startswith("error:")


def test_rejects_bad_shape(tmp_path: Path) -> None:
    tool = chart.RenderChartTool(workspace=tmp_path)
    assert tool.run(spec={"data": {"values": []}}).startswith("error:")  # no mark/layer/…
    assert tool.run(spec={"mark": "bar", "encoding": {}}).startswith("error:")  # no data


def test_unknown_format(tmp_path: Path) -> None:
    assert chart.RenderChartTool(workspace=tmp_path).run(spec=_SPEC, format="pdf").startswith("error:")


def test_png_uses_static_renderer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_static(spec: dict, out: Path, fmt: str) -> None:
        out.write_bytes(b"PNGDATA")

    monkeypatch.setattr(chart, "_render_static", fake_static)
    out = tmp_path / "c.png"
    res = chart.RenderChartTool(workspace=tmp_path).run(spec=_SPEC, format="png", out=str(out))
    assert "saved png chart" in res and out.read_bytes() == b"PNGDATA"


def test_png_missing_extra_gives_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def raise_import(spec: dict, out: Path, fmt: str) -> None:
        raise ImportError("no vl_convert")

    monkeypatch.setattr(chart, "_render_static", raise_import)
    res = chart.RenderChartTool(workspace=tmp_path).run(spec=_SPEC, format="png")
    assert res.startswith("error:") and "viz-vega" in res
