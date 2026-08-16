"""`refuse_write` calls itself "the single gate every file-writing tool passes". It had 3 callers of 8.

Its own docstring says why it exists: an earlier denylist lived inside `WriteRegion` and the writers
read `if region is not None`, so the never-writable set was unreachable in the default configuration
— "code that looks like a guard and never executes". The fix made the gate mandatory and then five
writers were added over time that do not call it.

Two different severities, and conflating them would overstate the first or understate the second:

  ESCAPES THE WORKSPACE — `generate_image`, `text_to_speech`, `browser screenshot`. None of these
  three took a `workspace` at all, so there was nothing to resolve an output path against and an
  absolute `out` wrote wherever it pointed.

  CONFINED BUT UNGATED — `render_chart`, `download_media`. Both resolve into the workspace, so no
  escape; both then skip `refuse_write`, so they reach `.git/` and `.chimera/` inside it and ignore
  any `--write-region` the run declared.

The last test is the one that matters in a year: an AST walk that fails the build when a tool in
`chimera/tools/` writes to disk without passing the gate.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

import pytest

from chimera.tools.chart import RenderChartTool
from chimera.tools.download import DownloadMediaTool
from chimera.tools.media import ImageGenTool, TextToSpeechTool
from chimera.tools.workspace import PathEscapesWorkspaceError
from chimera.tools.write_region import WriteRegion

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1] / "chimera" / "tools"

#: Calls that put bytes on disk. `atomic_write_text` is the project's own helper and lands here too.
_WRITE_CALLS = {"write_text", "write_bytes", "atomic_write_text", "mkstemp"}


# --- the three that escaped the workspace ---------------------------------------------------------


def test_image_generation_cannot_write_outside_the_workspace(tmp_path: pathlib.Path) -> None:
    """An absolute `out` used to be taken verbatim. The tool had no workspace to resolve against."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    escape = tmp_path / "outside" / "escaped.png"

    # Refused by raising, which is the convention `write_file` already sets for an escaping path.
    with pytest.raises(PathEscapesWorkspaceError):
        ImageGenTool(workspace).run(prompt="x", out=str(escape))

    assert not escape.exists(), "the tool wrote outside the workspace"


def test_text_to_speech_cannot_write_outside_the_workspace(tmp_path: pathlib.Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    escape = tmp_path / "outside" / "escaped.mp3"

    with pytest.raises(PathEscapesWorkspaceError):
        TextToSpeechTool(workspace).run(text="hello", out=str(escape))

    assert not escape.exists()


def test_the_browser_screenshot_is_confined(tmp_path: pathlib.Path) -> None:
    """`screenshot` handed its path straight to the driver. A fake driver records what it was
    given, which is the only way to see the confinement without Playwright."""
    from chimera.tools.browser import BrowserTool

    workspace = tmp_path / "ws"
    workspace.mkdir()
    taken: list[str] = []

    class _Driver:
        def screenshot(self, path: str) -> None:
            taken.append(path)

        def navigate(self, url: str) -> Any:  # pragma: no cover - not reached here
            return []

    tool = BrowserTool(_Driver(), workspace=workspace)

    # This one reports rather than raises: `run` wraps its dispatch in a broad except, so the
    # escape surfaces as an ordinary tool error. What matters either way is that the driver never
    # sees the path.
    refused = tool.run(action="screenshot", path=str(tmp_path / "outside" / "shot.png"))
    assert "escapes" in refused
    assert not taken, "the driver was handed a path outside the workspace"

    tool.run(action="screenshot", path="shots/ok.png")  # a relative one still works
    assert taken and pathlib.Path(taken[0]).is_relative_to(workspace)


# --- the two that were confined but ungated -------------------------------------------------------


def test_a_chart_cannot_be_written_into_dot_git(tmp_path: pathlib.Path) -> None:
    """Inside the workspace is not the same as allowed. `.git/` is never writable, whatever the
    region — that is the whole reason `refuse_write` exists separately from `WriteRegion`."""
    workspace = tmp_path / "ws"
    (workspace / ".git").mkdir(parents=True)
    spec = {"mark": "bar", "data": {"values": [{"a": 1, "b": 2}]}, "encoding": {}}

    out = RenderChartTool(workspace).run(spec=spec, out=".git/HEAD", format="html")

    assert "refused" in out.lower()
    assert not (workspace / ".git" / "HEAD").exists()


def test_a_chart_respects_a_declared_write_region(tmp_path: pathlib.Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    region = WriteRegion(["docs/*"], workspace)
    spec = {"mark": "bar", "data": {"values": [{"a": 1, "b": 2}]}, "encoding": {}}

    refused = RenderChartTool(workspace, write_region=region).run(
        spec=spec, out="src/chart.html", format="html"
    )
    allowed = RenderChartTool(workspace, write_region=region).run(
        spec=spec, out="docs/chart.html", format="html"
    )

    assert "refused" in refused.lower()
    assert "refused" not in allowed.lower()
    assert (workspace / "docs" / "chart.html").exists()


def test_download_respects_the_region(tmp_path: pathlib.Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    region = WriteRegion(["media/*"], workspace)

    out = DownloadMediaTool(workspace, write_region=region).run(
        url="https://example.test/v.mp4", out_dir="elsewhere"
    )

    assert "refused" in out.lower()


# --- the gate that keeps the next writer honest ---------------------------------------------------

#: Write sites that legitimately skip `refuse_write`, each with the reason. Listing the EXEMPTIONS
#: rather than the obligations is deliberate and is the same shape the governance gate uses: a list
#: of things to check fails open, and the site nobody remembered to add is exactly the one that
#: breaks. This list is how a reviewer sees a new escape hatch in a diff.
EXEMPT: dict[str, str] = {
    "browser.py:_html_to_markdown": "mkstemp in the system temp dir; never a caller-supplied path",
    "chart.py:_render_static": "a helper; its only caller (RenderChartTool.run) holds the gate",
    "code.py:run": "writes the script into the sandbox's own scratch dir, not the workspace",
    "workspace.py:atomic_write_text": "the primitive itself — the gate calls IT, not the reverse",
}


def _ungated_writers() -> list[str]:
    """`file.py:function` for every write in `chimera/tools/` with no `refuse_write` beside it."""
    offenders: list[str] = []
    for path in sorted(TOOLS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            names = {
                getattr(inner.func, "attr", "") or getattr(inner.func, "id", "")
                for inner in ast.walk(node)
                if isinstance(inner, ast.Call)
            }
            if not (names & _WRITE_CALLS) or "refuse_write" in names:
                continue
            key = f"{path.name}:{node.name}"
            if key not in EXEMPT:
                offenders.append(key)
    return offenders


def test_no_tool_writes_to_disk_without_passing_the_gate() -> None:
    """The structural half. `refuse_write` says it is "the single gate every file-writing tool
    passes" — it had three callers out of eight, and nothing made that visible."""
    ungated = _ungated_writers()

    assert not ungated, (
        f"a tool writes to disk without refuse_write: {ungated} — route it through the gate, or "
        "add it to EXEMPT with the reason it does not need one"
    )


def test_the_gate_check_is_not_vacuous(tmp_path: pathlib.Path) -> None:
    """Proof the walk can still see an offender: the same analysis over a fabricated tool."""
    source = ast.parse(
        "def run(self, **kwargs):\n"
        "    out = resolve_in_workspace(self.workspace, kwargs['out'])\n"
        "    out.write_text('data')\n"
    )
    node = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef))
    names = {
        getattr(inner.func, "attr", "") or getattr(inner.func, "id", "")
        for inner in ast.walk(node)
        if isinstance(inner, ast.Call)
    }

    assert names & _WRITE_CALLS
    assert "refuse_write" not in names


def test_every_exemption_still_points_at_real_code() -> None:
    """A stale exemption is a line saying "this was considered" about a site that no longer exists."""
    live: set[str] = set()
    for path in sorted(TOOLS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names = {
                    getattr(inner.func, "attr", "") or getattr(inner.func, "id", "")
                    for inner in ast.walk(node)
                    if isinstance(inner, ast.Call)
                }
                if names & _WRITE_CALLS:
                    live.add(f"{path.name}:{node.name}")

    assert not sorted(set(EXEMPT) - live), f"EXEMPT names sites that no longer write: {sorted(set(EXEMPT) - live)}"


@pytest.mark.parametrize("tool", ["render_chart", "download_media"])
def test_the_registry_hands_the_region_to_every_writer(tool: str, tmp_path: pathlib.Path) -> None:
    """The wiring half: a tool that accepts a region and is registered without one is a fence that
    exists in the class and not in the product."""
    from chimera.tools import default_registry

    region = WriteRegion(["docs/*"], tmp_path)
    registry = default_registry(tmp_path, write_region=region)

    instance = next((t for t in registry.tools() if t.name == tool), None)
    assert instance is not None, f"{tool} is not registered"
    assert getattr(instance, "write_region", None) is region
