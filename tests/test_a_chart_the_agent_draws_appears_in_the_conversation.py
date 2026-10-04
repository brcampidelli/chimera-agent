"""A chart `render_chart` wrote reaches the conversation as its spec — inert JSON the screen draws.

Study 29, P6.1. The tool wrote `chart.html` and the conversation showed a row saying so: to see the
chart the person had to find the file and open it in the viewer, and the viewer's copy drew only by
loading Vega from a CDN. The turn now carries a `chart` frame with the Vega-Lite spec, and the Code
screen draws it with the app's own Vega.

Pinned here: the tool tells a bound sink about each chart only AFTER the file exists (a refused or
failed render announces nothing), headless runs announce to nobody, the frame withholds a spec that
names something to load or follow (so the screen is never handed a fetch) and one too large to
carry, and the turn's stream and its run log both have the frame — it is part of the answer, unlike
a browser picture, so a screen that reconnects gets it back.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.api.code_api import CodeSeams, assemble_registry
from chimera.config import Settings
from chimera.providers.gateway import LLMGateway
from chimera.tools import chart
from chimera.tools.chart import CHART_FRAME_MAX_BYTES, ChartAnnouncer, RenderChartTool, chart_frame

SPEC: dict[str, Any] = {
    "title": "Sales",
    "data": {"values": [{"a": "A", "b": 5}, {"a": "B", "b": 8}]},
    "mark": "bar",
    "encoding": {"x": {"field": "a", "type": "nominal"}, "y": {"field": "b", "type": "quantitative"}},
}


# ------------------------------------------------------------------ the tool


def test_the_tool_announces_a_chart_after_writing_it(tmp_path: Path) -> None:
    tool = RenderChartTool(workspace=tmp_path)
    seen: list[dict[str, Any]] = []
    tool.on_chart = lambda frame: seen.append({**frame, "exists": (tmp_path / frame["path"]).exists()})

    out = tool.run(spec=SPEC, out="charts/sales.html")

    assert out.startswith("saved html chart")
    assert len(seen) == 1
    frame = seen[0]
    assert frame["exists"], "announced before the file was on disk"
    assert frame["path"] == "charts/sales.html", "the path is the project's, not the machine's"
    assert frame["format"] == "html" and frame["title"] == "Sales"
    assert frame["spec"] == SPEC and frame["withheld"] is None


def test_a_render_that_did_not_happen_announces_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = RenderChartTool(workspace=tmp_path)
    seen: list[Any] = []
    tool.on_chart = seen.append

    assert tool.run(spec={"mark": "bar"}).startswith("error:")  # bad shape
    assert tool.run(spec=SPEC, format="pdf").startswith("error:")  # unknown format

    def no_extra(spec: dict[str, Any], out: Path, fmt: str) -> None:
        raise ImportError("vl_convert")

    monkeypatch.setattr(chart, "_render_static", no_extra)
    assert tool.run(spec=SPEC, format="png").startswith("error:")  # the extra is missing
    assert seen == [], "a chart that was never written was shown as drawn"


def test_a_screen_that_fails_to_hear_does_not_fail_the_tool(tmp_path: Path) -> None:
    tool = RenderChartTool(workspace=tmp_path)

    def broken(_frame: dict[str, Any]) -> None:
        raise RuntimeError("socket gone")

    tool.on_chart = broken
    assert tool.run(spec=SPEC).startswith("saved html chart")


def test_the_announcer_drops_charts_until_a_screen_binds() -> None:
    announcer = ChartAnnouncer()
    announcer({"path": "x"})  # nobody bound: dropped, no error
    got: list[str] = []
    announcer.emit = lambda frame: got.append(frame["path"])
    announcer({"path": "chart.html"})
    assert got == ["chart.html"]


# ------------------------------------------------------------------ what the frame carries


@pytest.mark.parametrize(
    "spec",
    [
        {**SPEC, "data": {"url": "https://example.com/data.csv"}},
        {**SPEC, "data": {"url": "local.csv"}},
        {**SPEC, "mark": "image", "encoding": {**SPEC["encoding"], "url": {"field": "img"}}},
        {**SPEC, "encoding": {**SPEC["encoding"], "href": {"field": "link"}}},
        {"layer": [SPEC, {**SPEC, "data": {"url": "x.json"}}], "data": SPEC["data"]},
    ],
    ids=["remote-data", "local-data", "image-url", "link", "nested-layer"],
)
def test_a_spec_that_reaches_outside_itself_is_never_handed_to_the_screen(spec: dict[str, Any]) -> None:
    frame = chart_frame(spec, "chart.html", "html")
    assert frame["withheld"] == "external"
    assert frame["spec"] is None
    assert "example.com" not in json.dumps(frame)


@pytest.mark.parametrize(
    "spec",
    [
        {**SPEC, "data": {"values": [{"url": "/home", "visits": 3}, {"url": "/about", "visits": 1}]}},
        {**SPEC, "data": {"values": [{"href": "https://example.com", "b": 1}]}},
        {"layer": [{**SPEC, "data": {"values": [{"url": "/home", "b": 1}]}}]},
        {**SPEC, "data": {"name": "rows"}, "datasets": {"rows": [{"url": "/home", "b": 1}]}},
        {
            **SPEC,
            "transform": [
                {"lookup": "a", "from": {"data": {"values": [{"a": "A", "url": "/x"}]}, "key": "a"}}
            ],
        },
    ],
    ids=["column-named-url", "column-named-href", "layer-rows", "datasets-rows", "lookup-rows"],
)
def test_a_column_named_url_in_inline_rows_is_not_reaching_outside(spec: dict[str, Any]) -> None:
    # Inline rows are inert: they reach outside only through a `url`/`href` encoding channel, which
    # is a key of the spec and still caught. A "visits per url" chart is not withheld as external.
    frame = chart_frame(spec, "chart.html", "html")
    assert frame["withheld"] is None
    assert frame["spec"] == spec


def test_rows_drawn_as_links_or_images_are_still_withheld() -> None:
    rows = {"values": [{"a": "A", "b": 1, "link": "https://example.com"}]}
    as_link = {**SPEC, "data": rows, "encoding": {**SPEC["encoding"], "href": {"field": "link"}}}
    as_image = {**SPEC, "data": rows, "mark": "image", "encoding": {**SPEC["encoding"], "url": {"field": "link"}}}
    lookup_by_url = {**SPEC, "transform": [{"lookup": "a", "from": {"data": {"url": "x.csv"}, "key": "a"}}]}
    for spec in (as_link, as_image, lookup_by_url):
        assert chart_frame(spec, "chart.html", "html")["withheld"] == "external"


def test_a_spec_too_large_to_carry_is_withheld_and_says_how_large() -> None:
    rows = [{"a": f"row-{i}", "b": i} for i in range(CHART_FRAME_MAX_BYTES // 10)]
    frame = chart_frame({**SPEC, "data": {"values": rows}}, "big.html", "html")
    assert frame["withheld"] == "large" and frame["spec"] is None
    assert frame["bytes"] > CHART_FRAME_MAX_BYTES


@pytest.mark.parametrize(
    ("title", "shown"),
    [({"text": "Revenue", "subtitle": "2026"}, "Revenue"), (["Line one", "line two"], "Line one line two"), ("", None)],
)
def test_the_title_is_read_in_each_form_vega_lite_allows(title: Any, shown: str | None) -> None:
    assert chart_frame({**SPEC, "title": title}, "c.html", "html")["title"] == shown


# ------------------------------------------------------------------ through the registry the Code screen builds


def _registry(tmp_path: Path, sink: Any) -> Any:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))  # type: ignore[call-arg]
    registry, _ = assemble_registry(
        CodeSeams(), ws, settings, LLMGateway(), steps=4, surface="api:turn", chart_sink=sink
    )
    return registry


def test_the_assembly_hands_render_chart_its_sink_only_when_there_is_one(tmp_path: Path) -> None:
    sink = ChartAnnouncer()
    got: list[str] = []
    sink.emit = lambda frame: got.append(frame["path"])
    registry = _registry(tmp_path / "a", sink)
    registry.get("render_chart").run(spec=SPEC)
    assert got == ["chart.html"]

    headless = _registry(tmp_path / "b", None)
    tool = headless.get("render_chart")
    assert tool.run(spec=SPEC).startswith("saved")
    assert got == ["chart.html"], "a headless run announced a chart"


# ------------------------------------------------------------------ the turn's stream, and the run log


def test_the_turn_streams_the_chart_and_the_run_log_keeps_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.core.agent import AgentResult
    from chimera.interface import ChatSession
    from chimera.orchestration import runlog

    class _Charting:
        """An agent whose one step draws a chart through the registry it was handed."""

        def __init__(self, *_a: Any, **_k: Any) -> None:
            self.tools = _a[1] if len(_a) > 1 else _k.get("tools")

        def run(self, task: str, **_: Any) -> AgentResult:
            self.tools.get("render_chart").run(spec=SPEC, out="sales.html")
            return AgentResult(
                answer="drawn",
                steps=1,
                stopped_reason="final",
                transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "drawn"}],
                model="test/model",
            )

    import chimera.core

    monkeypatch.setattr(chimera.core, "Agent", _Charting, raising=True)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    from chimera.config import get_settings

    get_settings.cache_clear()
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))  # type: ignore[call-arg]
    client = TestClient(build_api_app(lambda: ChatSession(_Charting()), workspace=ws, settings=settings))

    response = client.post("/api/code/turn", json={"message": "chart the sales"})
    event, charts, turn_id = "", [], ""
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            payload = json.loads(line[len("data: ") :])
            if event == "session":
                turn_id = payload.get("turn_id", "")
            if event == "chart":
                charts.append(payload)

    assert len(charts) == 1
    assert charts[0]["spec"] == SPEC and charts[0]["path"] == "sales.html" and charts[0]["seq"] > 0
    assert (ws / "sales.html").exists()

    logged = [f for f in runlog.frames(Path(settings.home), turn_id, area="code") if f.get("event") == "chart"]
    assert len(logged) == 1 and logged[0]["spec"] == SPEC, "a reconnecting screen would lose the chart"
