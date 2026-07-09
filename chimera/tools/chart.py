"""render_chart — render a Vega-Lite spec to an inert, inspectable chart artifact.

Chimera's differentiated, *safe* alternative to "the LLM writes matplotlib code we execute": a
**Vega-Lite spec is declarative JSON data, not code**. It can be inspected and shape-checked before
anything renders, it can't touch the filesystem or shell, and it's diffable/storable/re-renderable —
a strictly better artifact for the standard statistical charts Vega-Lite covers (bar/line/area/
scatter/histogram/heatmap/faceted/layered/interactive). Altair is exactly this idea (a pure-Python
Vega-Lite spec emitter); here the *agent's LLM* emits the spec and this tool renders it.

Rendering:
  * ``html`` (default) — a self-contained page that embeds the spec + the Vega/Vega-Lite/vega-embed
    scripts from a CDN. **Zero extra Python dependencies** — Chimera ships only a string.
  * ``png`` / ``svg`` — only if the optional ``viz-vega`` extra (``vl-convert-python``, a Rust+V8
    binary wheel) is installed; otherwise the tool returns a clear install hint.

For arbitrary/custom charts beyond Vega-Lite's grammar (bespoke matplotlib art, 3D, etc.), use the
``data_visualization`` skill, which writes plotting code for the code sandbox.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.tools.base import Tool
from chimera.tools.workspace import resolve_in_workspace

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


def _parse_spec(raw: Any) -> dict[str, Any] | None:
    """Accept a Vega-Lite spec as a dict or a JSON string. Returns the dict, or None if unusable."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None
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


def _html(spec: dict[str, Any]) -> str:
    """A self-contained HTML page embedding the spec — renders client-side via the Vega CDN."""
    scripts = "\n".join(
        f'  <script src="https://cdn.jsdelivr.net/npm/{name}@{ver}"></script>' for name, ver in _VEGA_CDN
    )
    spec_json = json.dumps(spec, indent=2)
    return (
        "<!doctype html>\n<html>\n<head>\n  <meta charset=\"utf-8\">\n"
        f"{scripts}\n</head>\n<body>\n  <div id=\"vis\"></div>\n  <script>\n"
        f"    const spec = {spec_json};\n"
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

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()

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
        out = resolve_in_workspace(self.workspace, str(kwargs.get("out") or f"chart.{fmt}"))
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
        return f"saved {fmt} chart to {out}"
