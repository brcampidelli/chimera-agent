"""A chart spec that asks Vega to make up millions of rows is withheld from the screen.

Study 29, P6.1, from an adversarial review. The conversation draws a chart's spec in the app's own
document, on the thread that also runs the composer and the approval card, and draws it as soon as
the frame arrives. `chart_frame` limited a spec's bytes and its URLs, not its work, and a spec is
small for the work it can ask for: `{"data": {"sequence": {"start": 0, "stop": 4000000}}, ...}` is
125 bytes and held Vega for 29.6 s and 2.19 GB of heap headless, before any SVG node existed. The
model can write that by accident ("plot sin(x) from 0 to 1e6 in steps of 0.01"), a guest's turn
draws on the owner's screen, and the frame is kept, so every screen that follows the conversation
draws it again.

Pinned here: the generators measured to stall the screen are read from the spec without running it
(`generated_rows`), a spec over `CHART_MAX_GENERATED_ROWS` reaches the screen as `withheld: heavy`
with no spec in the frame, and the file is still written in full. The cases live in a fixture the
desktop's copy of the estimate (`generatedRows` in `lib/chart/spec.ts`) is tested against too, so
the two cannot drift apart without one of the suites going red.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from chimera.tools.chart import (
    CHART_MAX_GENERATED_ROWS,
    RenderChartTool,
    chart_frame,
    generated_rows,
)

FIXTURE = Path(__file__).resolve().parents[1] / "apps" / "desktop" / "src" / "lib" / "chart" / "fixtures" / "generated-rows.json"
CASES: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))

# The reviewer's spec, byte for byte: what the model writes for "plot every x up to four million".
SEQUENCE: dict[str, Any] = {
    "data": {"sequence": {"start": 0, "stop": 4000000, "as": "x"}},
    "mark": "point",
    "encoding": {"x": {"field": "x", "type": "quantitative"}},
}


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_the_rows_a_spec_makes_up_are_read_from_the_spec(case: dict[str, Any]) -> None:
    expected = math.inf if case["rows"] == "inf" else case["rows"]
    assert generated_rows(case["spec"]) == expected


def test_the_fixture_covers_both_sides_of_the_budget() -> None:
    # A fixture whose every case sat under the budget would pass with the check deleted.
    rows = [math.inf if case["rows"] == "inf" else case["rows"] for case in CASES]
    assert any(r > CHART_MAX_GENERATED_ROWS for r in rows)
    assert any(r == math.inf for r in rows)
    assert any(0 < r <= CHART_MAX_GENERATED_ROWS for r in rows)


def test_a_small_spec_that_makes_up_millions_of_rows_is_never_handed_to_the_screen() -> None:
    frame = chart_frame(SEQUENCE, "sin.html", "html")
    assert frame["bytes"] < 200, "small enough that the byte limit alone let it through"
    assert frame["withheld"] == "heavy"
    assert frame["spec"] is None


@pytest.mark.parametrize("case", [c for c in CASES if c["rows"] == "inf" or c["rows"] > CHART_MAX_GENERATED_ROWS], ids=lambda c: c["name"])
def test_every_heavy_case_is_withheld_with_its_spec_left_out(case: dict[str, Any]) -> None:
    frame = chart_frame(case["spec"], "chart.html", "html")
    assert (frame["withheld"], frame["spec"]) == ("heavy", None)


def test_a_chart_within_the_budget_is_still_handed_over() -> None:
    spec = {**SEQUENCE, "data": {"sequence": {"start": 0, "stop": CHART_MAX_GENERATED_ROWS, "as": "x"}}}
    frame = chart_frame(spec, "sin.html", "html")
    assert frame["withheld"] is None and frame["spec"] == spec


def test_a_spec_that_reaches_outside_is_still_called_external_first() -> None:
    # Two reasons, one word: the one that says the screen would have been made to fetch something.
    spec = {**SEQUENCE, "encoding": {**SEQUENCE["encoding"], "href": {"field": "x"}}}
    assert chart_frame(spec, "c.html", "html")["withheld"] == "external"


def test_the_tool_still_writes_a_heavy_chart_in_full(tmp_path: Path) -> None:
    tool = RenderChartTool(workspace=tmp_path)
    seen: list[dict[str, Any]] = []
    tool.on_chart = seen.append

    out = tool.run(spec=SEQUENCE, out="sin.html")

    assert out.startswith("saved html chart")
    assert '"stop": 4000000' in (tmp_path / "sin.html").read_text(encoding="utf-8")
    assert [(f["withheld"], f["spec"]) for f in seen] == [("heavy", None)]
