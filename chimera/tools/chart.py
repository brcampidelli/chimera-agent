"""render_chart — render a Vega-Lite spec to an inert, inspectable chart artifact.

Chimera's differentiated, *safe* alternative to "the LLM writes matplotlib code we execute": a
**Vega-Lite spec is declarative JSON data, not code**. It can be inspected and shape-checked before
anything renders, it can't touch the filesystem or shell, and it's diffable/storable/re-renderable —
a strictly better artifact for the standard statistical charts Vega-Lite covers (bar/line/area/
scatter/histogram/heatmap/faceted/layered/interactive). Altair is exactly this idea (a pure-Python
Vega-Lite spec emitter); here the *agent's LLM* emits the spec and this tool renders it.

Rendering:
  * ``html`` (default) — a self-contained page that embeds the spec + the Vega/Vega-Lite/vega-embed
    scripts from a CDN. **Zero extra Python dependencies** — Chimera ships only a string. Inside the
    app the CDN is never used: the conversation draws the spec it is sent (``chart_frame``) and the
    viewer draws the spec it finds in the page, both with the app's own Vega.
  * ``png`` / ``svg`` — only if the optional ``viz-vega`` extra (``vl-convert-python``, a Rust+V8
    binary wheel) is installed; otherwise the tool returns a clear install hint.

For arbitrary/custom charts beyond Vega-Lite's grammar (bespoke matplotlib art, 3D, etc.), use the
``data_visualization`` skill, which writes plotting code for the code sandbox.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from chimera.tools.base import Tool
from chimera.tools.workspace import resolve_for
from chimera.tools.write_region import WriteRegion, refuse_write

_log = logging.getLogger(__name__)

# Pinned CDN majors — the client-side render path, no Python dep. jsDelivr serves the latest of each.
_VEGA_CDN = (
    ("vega", "5"),
    ("vega-lite", "5"),
    ("vega-embed", "6"),
)

_INSTALL_HINT = (
    "error: PNG/SVG chart rendering needs the 'viz-vega' extra — install with: "
    "pip install 'chimera-agent[viz-vega]' (HTML output needs no extra)"
)

# A single-view spec has a `mark`; composite specs use one of these operators instead.
_CHART_KEYS = ("mark", "layer", "hconcat", "vconcat", "concat", "facet", "repeat")

# The most of a spec the conversation carries, serialised compactly. A chart's data can be the whole
# dataset (`data.values`), and the frame is kept twice — on the session bus and in the run log — and
# sent to every screen watching. Past this the card says the chart is in the file instead.
CHART_FRAME_MAX_BYTES = 128_000

# The keys through which a Vega-Lite spec reaches OUTSIDE itself: `data.url` / a lookup's `from`
# (a fetch), an image mark's `url` channel (a fetch), and `href` (a link a click follows). The screen
# draws only what the spec carries inline, so a spec naming any of these is not drawn there.
_REACHING_KEYS = frozenset({"url", "href"})


# The most rows a spec may make up on its own and still be drawn on the screen. The screen draws in
# the app's own document, on the thread that also runs the composer and the approval card, and a
# spec of a hundred bytes can ask Vega for millions of rows: `{"sequence": {"start": 0, "stop":
# 4000000}}` took 29.6 s and 2.19 GB of heap headless, before a single SVG node. Measured with this
# repository's Vega at 200,000 made-up rows, each generator read below cost 2 to 12 s headless.
# Inline rows are not counted: their number is already bounded by CHART_FRAME_MAX_BYTES.
CHART_MAX_GENERATED_ROWS = 50_000

# Strings under these keys are text a person reads, not expressions Vega runs, so a title such as
# "the sequence(1, 10)" is not read as a call.
_TEXT_KEYS = frozenset({"title", "subtitle", "text", "description"})

# The expression functions that make something whose size is an argument: `sequence(start, stop,
# step)` an array, `pad(text, length)` a string. Either can sit in a `calculate`, a `filter`, a
# signal. A call whose arguments are not all number literals cannot be sized, and counts as infinite.
_SEQUENCE_CALL = re.compile(r"\bsequence\s*\(")
_SEQUENCE_LITERAL = re.compile(r"\bsequence\s*\(([-+0-9.eE\s,]*)\)")
_PAD_CALL = re.compile(r"\bpad\s*\(")
_PAD_LITERAL = re.compile(r"\bpad\s*\([^,()]*,\s*([-+0-9.eE]+)\s*[,)]")


def _number(value: Any) -> float | None:
    """``value`` as a finite number, or None when it is anything else (a signal, a string, NaN)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _count(value: Any) -> float:
    """A count the spec states: the number, or infinite when it is not one."""
    number = _number(value)
    return math.inf if number is None else max(0.0, number)


def _span(start: Any, stop: Any, step: Any) -> float:
    """How many values ``range(start, stop, step)`` holds; infinite when it cannot be told."""
    a, b, s = _number(start), _number(stop), _number(step)
    if a is None or b is None or s is None or s == 0:
        return math.inf
    return float(max(0, math.ceil((b - a) / s)))


def _literals(text: str) -> list[float] | None:
    """The comma-separated number literals in ``text``, or None when any part is not one."""
    try:
        return [float(part) for part in text.split(",")]
    except ValueError:
        return None


def _expression_rows(text: str) -> float:
    """What the ``sequence()`` and ``pad()`` calls in one string make."""
    total = 0.0
    calls = len(_SEQUENCE_CALL.findall(text))
    sized = _SEQUENCE_LITERAL.findall(text)
    if len(sized) != calls:
        return math.inf
    for args in sized:
        values = _literals(args)
        if values is None or not 1 <= len(values) <= 3:
            return math.inf
        if len(values) == 1:
            total += _span(0, values[0], 1)
        else:
            total += _span(values[0], values[1], values[2] if len(values) == 3 else 1)
    pads = len(_PAD_CALL.findall(text))
    lengths = _PAD_LITERAL.findall(text)
    if len(lengths) != pads:
        return math.inf
    for length in lengths:
        values = _literals(length)
        total += math.inf if values is None else _count(values[0])
    return total


def _node_rows(node: dict[str, Any]) -> float:
    """What one object of the spec makes up, read from its own keys."""
    total = 0.0
    sequence = node.get("sequence")
    if isinstance(sequence, dict):  # a data generator
        total += _span(sequence.get("start", 0), sequence.get("stop"), sequence.get("step", 1))
    keyvals = node.get("keyvals")
    if isinstance(keyvals, dict):  # impute's key values, as a sequence
        total += _span(keyvals.get("start", 0), keyvals.get("stop"), keyvals.get("step", 1))
    if "density" in node:  # one row per step (200 at most when no step count is said)
        total += _count(node.get("steps", node.get("maxsteps", 200)))
    if "quantile" in node:  # one row per probability: 1/step of them
        step = _number(node.get("step", 0.01))
        total += math.ceil(1 / step) if step is not None and step > 0 else math.inf
    binning = node.get("bin")
    if isinstance(binning, dict):
        step, extent = binning.get("step"), binning.get("extent")
        if step is None:  # without a step, `maxbins` bounds the bins (10 when it is not said)
            total += _count(binning.get("maxbins", 10))
        elif isinstance(extent, list) and len(extent) == 2:
            total += _span(extent[0], extent[1], step)
        # A step over the data's own extent cannot be sized from the spec. The screen's time budget
        # is what stops that one (`apps/desktop/src/lib/chart/preflight.ts`).
    tick_count = node.get("tickCount")
    if tick_count is not None and not isinstance(tick_count, (str, dict)):
        # A time interval (a string, or `{interval, step}`) depends on the data, like a bin step.
        total += _count(tick_count)
    return total


def generated_rows(node: Any, *, in_data: bool = False) -> float:
    """How many rows, marks or characters a spec asks Vega to make up beyond what it carries.

    Read from the spec without running it, for the generators measured to stall the screen: data
    ``sequence``, impute ``keyvals``, ``density`` steps, ``quantile`` steps, ``bin`` counts,
    ``tickCount``, and the ``sequence()`` and ``pad()`` expression calls. A value it cannot size (a
    signal where a number goes, a call with a computed argument) counts as infinite: the spec is
    withheld rather than guessed small. Inline rows are skipped as in :func:`_reaches_outside`.
    Mirrors ``generatedRows`` in ``apps/desktop/src/lib/chart/spec.ts``.
    """
    if isinstance(node, dict):
        total = _node_rows(node)
        for key, value in node.items():
            if key == "datasets" or (in_data and key == "values"):
                continue
            if key in _TEXT_KEYS and isinstance(value, (str, list)):
                continue
            total += generated_rows(value, in_data=key == "data")
        return total
    if isinstance(node, list):
        return sum((generated_rows(item, in_data=in_data) for item in node), 0.0)
    if isinstance(node, str):
        return _expression_rows(node)
    return 0.0


def _finite(node: Any) -> Any:
    """``node`` with every NaN and infinity replaced by None (``null``).

    ``json.loads`` accepts ``NaN`` and ``Infinity`` and ``json.dumps`` writes them back, but they are
    not JSON: the page reads its spec with ``JSON.parse`` and the client parses the ``chart`` frame
    the same way, and both refused it — the saved page stopped drawing and the card never came.
    Vega reads a null value as missing, as it read NaN.
    """
    if isinstance(node, float) and not math.isfinite(node):
        return None
    if isinstance(node, dict):
        return {key: _finite(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_finite(item) for item in node]
    return node


def _parse_spec(raw: Any) -> dict[str, Any] | None:
    """Accept a Vega-Lite spec as a dict or a JSON string. Returns the dict, or None if unusable."""
    if isinstance(raw, dict):
        return {key: _finite(value) for key, value in raw.items()}
    if isinstance(raw, str) and raw.strip():
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return {key: _finite(value) for key, value in obj.items()} if isinstance(obj, dict) else None
    return None


def _validate_shape(spec: dict[str, Any]) -> str | None:
    """Lightweight structural check (NOT full Vega-Lite schema conformance).

    Catches the common LLM mistakes (no chart definition, no data) without bundling the ~1 MB
    Vega-Lite schema or a jsonschema dependency. Returns an error string, or None if the shape is OK.
    """
    if not any(key in spec for key in _CHART_KEYS):
        return f"no chart definition (expected one of: {', '.join(_CHART_KEYS)})"
    if "mark" in spec and "encoding" not in spec:
        return "a single-view chart (has 'mark') needs an 'encoding'"
    if "data" not in spec and "datasets" not in spec:
        return "no data ('data' or 'datasets') in the spec"
    return None


def _reaches_outside(node: Any, *, in_data: bool = False) -> bool:
    """Whether a spec names anything to load or follow, at any depth.

    Inline rows are not read: ``values`` under a ``data`` and the tables in ``datasets`` are inert,
    and a column that happens to be called ``url`` (a "visits per url" chart) loads nothing. Rows
    reach outside only through a ``url``/``href`` encoding channel or an image mark's ``url``, and
    those are keys of the spec, still caught here. Mirrors ``reachesOutside`` in
    ``apps/desktop/src/lib/chart/spec.ts``.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _REACHING_KEYS:
                return True
            if key == "datasets" or (in_data and key == "values"):
                continue
            if _reaches_outside(value, in_data=key == "data"):
                return True
        return False
    if isinstance(node, list):
        return any(_reaches_outside(item, in_data=in_data) for item in node)
    return False


def chart_frame(spec: dict[str, Any], path: str, fmt: str) -> dict[str, Any]:
    """What the conversation is told about a chart that was drawn: the spec, or why it is withheld.

    The screen draws the spec itself, with a renderer that loads nothing (the app's own Vega, no
    network loader, no eval). Three kinds of spec are not handed to it, and the frame says which, so
    the card can say so instead of drawing a blank or half a chart:

    * ``external`` — it names a URL or a link. The renderer would refuse to load it anyway; refusing
      here as well means a spec written to make the screen fetch something never reaches a screen.
    * ``heavy`` — it asks Vega to make up more than :data:`CHART_MAX_GENERATED_ROWS` rows (see
      :func:`generated_rows`). Drawn, it would hold the app's window for seconds to minutes; the
      file draws it in a browser of its own.
    * ``large`` — its JSON is over :data:`CHART_FRAME_MAX_BYTES`. The file has it all.
    """
    compact = json.dumps(spec, separators=(",", ":"), ensure_ascii=False)
    size = len(compact.encode("utf-8"))
    title = spec.get("title")
    if isinstance(title, dict):  # Vega-Lite allows {"text": ..., ...}
        title = title.get("text")
    if isinstance(title, list):  # and a multi-line title as a list of lines
        title = " ".join(str(line) for line in title)
    withheld: str | None = None
    if _reaches_outside(spec):
        withheld = "external"
    elif generated_rows(spec) > CHART_MAX_GENERATED_ROWS:
        withheld = "heavy"
    elif size > CHART_FRAME_MAX_BYTES:
        withheld = "large"
    return {
        "path": path,
        "format": fmt,
        "title": str(title)[:200] if isinstance(title, str) and title.strip() else None,
        "spec": None if withheld else spec,
        "withheld": withheld,
        "bytes": size,
    }


class ChartAnnouncer:
    """A late-bound place to announce a drawn chart to a screen, like the browser's ``FrameAnnouncer``.

    The tool is built with the registry and the turn's ``emit`` exists a moment later, so this holds
    the slot. Unbound, a chart is announced to nobody — what every surface without a screen wants.
    """

    def __init__(self) -> None:
        self.emit: Callable[[dict[str, Any]], None] | None = None

    def __call__(self, frame: dict[str, Any]) -> None:
        if self.emit is not None:
            self.emit(frame)


def _script_safe_json(spec: dict[str, Any]) -> str:
    """The spec as JSON that cannot end the ``<script>`` element it sits in.

    The HTML parser ends a script at the first ``</script``, whatever the JavaScript around it says,
    and ``<!--`` changes how it reads what follows. A spec is the model's text, and a title such as
    ``</script><script>…`` used to close the element and go on as markup. Escaping ``<``, ``>`` and
    ``&`` as JSON unicode escapes leaves every value the same after ``JSON.parse`` and gives the
    parser nothing to stop on.
    """
    text = json.dumps(spec, indent=2)
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _html(spec: dict[str, Any]) -> str:
    """A self-contained HTML page embedding the spec — renders client-side via the Vega CDN.

    The spec sits in its own ``application/json`` element that the page's script reads. That is what
    lets the app's viewer find it and draw it with the app's own renderer instead of running the
    page (`apps/desktop/src/lib/chart/page.ts`), which is why the app's page policy no longer admits
    the CDN or ``'unsafe-eval'``. Opened in a browser, the page draws as it always did.
    """
    scripts = "\n".join(
        f'  <script src="https://cdn.jsdelivr.net/npm/{name}@{ver}"></script>' for name, ver in _VEGA_CDN
    )
    return (
        "<!doctype html>\n<html>\n<head>\n  <meta charset=\"utf-8\">\n"
        f"{scripts}\n</head>\n<body>\n  <div id=\"vis\"></div>\n"
        f'  <script type="application/json" id="chimera-chart-spec">{_script_safe_json(spec)}</script>\n'
        "  <script>\n"
        "    const spec = JSON.parse(document.getElementById('chimera-chart-spec').textContent);\n"
        "    vegaEmbed('#vis', spec).catch(console.error);\n"
        "  </script>\n</body>\n</html>\n"
    )


def _render_static(spec: dict[str, Any], out: Path, fmt: str) -> None:
    """Render to PNG/SVG via vl-convert-python (the `viz-vega` extra). Raises ImportError if absent."""
    import vl_convert as vlc  # lazy — the only place the heavy extra is touched

    if fmt == "png":
        out.write_bytes(vlc.vegalite_to_png(spec))
    else:
        out.write_text(vlc.vegalite_to_svg(spec), encoding="utf-8")


class RenderChartTool(Tool):
    name = "render_chart"
    description = (
        "Render a Vega-Lite chart spec (declarative JSON — inert, inspectable, not code) to a file. "
        "Args: spec (a Vega-Lite JSON object or string); optional format (html|png|svg, default html); "
        "optional out (path). HTML embeds the chart via a CDN and needs no extra; PNG/SVG need the "
        "'viz-vega' extra. For custom/arbitrary charts, use the data_visualization skill instead."
    )
    parameters = {
        "type": "object",
        "properties": {
            "spec": {
                "type": "object",
                "description": "A Vega-Lite spec (JSON object; a JSON string is also accepted).",
            },
            "format": {"type": "string", "description": "html (default), png, or svg."},
            "out": {"type": "string", "description": "Output file path (default chart.<ext>)."},
        },
        "required": ["spec"],
    }

    def __init__(
        self, workspace: Path | None = None, *, write_region: WriteRegion | None = None
    ) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.write_region = write_region
        # Set by a surface with a screen (the Code turn binds it to its stream), the way the
        # browser's `on_frame` is: told about each chart AFTER it was written, never before, so the
        # conversation shows only charts that exist on disk.
        self.on_chart: Callable[[dict[str, Any]], None] | None = None

    def run(self, **kwargs: Any) -> str:
        spec = _parse_spec(kwargs.get("spec"))
        if spec is None:
            return "error: render_chart needs a Vega-Lite 'spec' (a JSON object or string)"
        fmt = str(kwargs.get("format") or "html").lower()
        if fmt not in ("html", "png", "svg"):
            return f"error: unknown format {fmt!r} (use html, png, or svg)"
        shape_error = _validate_shape(spec)
        if shape_error:
            return f"error: invalid Vega-Lite spec: {shape_error}"
        out = resolve_for(self, str(kwargs.get("out") or f"chart.{fmt}"), verb="write")
        if err := refuse_write(self.workspace, out, self.write_region):
            return err
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            if fmt == "html":
                out.write_text(_html(spec), encoding="utf-8")
            else:
                _render_static(spec, out, fmt)
        except ImportError:
            return _INSTALL_HINT
        except Exception as exc:  # noqa: BLE001 — a render failure is a tool error, not a crash
            return f"error: chart render failed: {exc}"
        if self.on_chart is not None:
            try:
                shown = out.relative_to(self.workspace).as_posix()
            except ValueError:  # an approved write outside the project: the screen gets the full path
                shown = str(out)
            try:
                self.on_chart(chart_frame(spec, shown, fmt))
            except Exception:  # noqa: BLE001 — the file is written; a screen that failed to hear changes nothing
                _log.debug("render_chart: announcing the chart failed", exc_info=True)
        return f"saved {fmt} chart to {out}"
