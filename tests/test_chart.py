"""Tests for the render_chart Vega-Lite tool. Fakes only — HTML path is dep-free; static is monkeypatched."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


def test_html_keeps_the_spec_in_its_own_json_element(tmp_path: Path) -> None:
    # The viewer finds the spec here and draws it with the app's own Vega instead of running the
    # page (apps/desktop/src/lib/chart/page.ts), which is what let the page policy drop the CDN.
    out = tmp_path / "c.html"
    chart.RenderChartTool(workspace=tmp_path).run(spec=_SPEC, out=str(out))
    body = out.read_text(encoding="utf-8")
    start = body.index('<script type="application/json" id="chimera-chart-spec">') + len(
        '<script type="application/json" id="chimera-chart-spec">'
    )
    end = body.index("</script>", start)
    assert json.loads(body[start:end]) == _SPEC
    assert "JSON.parse(document.getElementById('chimera-chart-spec').textContent)" in body


def test_a_spec_cannot_close_the_script_it_sits_in(tmp_path: Path) -> None:
    # A spec is the model's text. With `json.dumps` alone, a title of `</script><script>…` ended
    # the element and the rest ran as markup — in the file opened in a browser, too.
    hostile = {**_SPEC, "title": "</script><script>alert(1)</script><!-- & -->"}
    out = tmp_path / "c.html"
    chart.RenderChartTool(workspace=tmp_path).run(spec=hostile, out=str(out))
    body = out.read_text(encoding="utf-8")
    assert "alert(1)</script>" not in body and "<!--" not in body
    start = body.index('id="chimera-chart-spec">') + len('id="chimera-chart-spec">')
    end = body.index("</script>", start)
    assert json.loads(body[start:end]) == hostile, "the escaping changed the value"


def test_the_desktop_fixture_is_what_the_tool_writes() -> None:
    # The viewer's tests read the spec out of this file (apps/desktop/src/lib/chart/page.test.ts),
    # so it must be the tool's real output: a template change here that the viewer does not follow
    # would quietly turn every new chart back into a page the policy blanks.
    fixture = Path(__file__).resolve().parents[1] / "apps/desktop/src/lib/chart/fixtures/render-chart-page.html"
    spec = {
        "title": "Sales",
        "data": {"values": [{"a": "A", "b": 5}, {"a": "B", "b": 8}]},
        "mark": "bar",
        "encoding": {"x": {"field": "a", "type": "nominal"}, "y": {"field": "b", "type": "quantitative"}},
    }
    assert fixture.read_bytes().decode("utf-8") == chart._html(spec)



def _strict(text: str) -> Any:
    """JSON as a browser's `JSON.parse` reads it: `NaN` and `Infinity` are not JSON."""

    def refuse(name: str) -> Any:
        raise ValueError(f"{name} is not JSON")

    return json.loads(text, parse_constant=refuse)


@pytest.mark.parametrize("given", ["string", "object"])
def test_a_value_json_cannot_carry_is_written_as_null(tmp_path: Path, given: str) -> None:
    # `json.loads` accepts NaN and Infinity, and `json.dumps` writes them back as bare words. The
    # page reads its spec with `JSON.parse`, which refuses them: the saved chart stopped drawing in a
    # browser, the viewer found no spec and fell back to a blank frame, and the `chart` frame was
    # dropped by the client's `JSON.parse` of the stream, so the card never appeared.
    raw = (
        '{"data": {"values": [{"a": "A", "b": NaN}, {"a": "B", "b": Infinity}, {"a": "C", "b": -Infinity}]},'
        ' "mark": "bar", "encoding": {"x": {"field": "a"}, "y": {"field": "b"}}}'
    )
    spec = raw if given == "string" else json.loads(raw)
    tool = chart.RenderChartTool(workspace=tmp_path)
    frames: list[dict[str, Any]] = []
    tool.on_chart = frames.append
    assert tool.run(spec=spec, out="c.html").startswith("saved html chart")

    body = (tmp_path / "c.html").read_text(encoding="utf-8")
    start = body.index('id="chimera-chart-spec">') + len('id="chimera-chart-spec">')
    page_spec = _strict(body[start : body.index("</script>", start)])
    assert page_spec["data"]["values"] == [{"a": "A", "b": None}, {"a": "B", "b": None}, {"a": "C", "b": None}]

    (frame,) = frames
    assert frame["withheld"] is None
    assert _strict(json.dumps(frame))["spec"] == page_spec
