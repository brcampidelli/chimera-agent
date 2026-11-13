"""Nothing reaches the model without a ceiling, and a cut always says how to go on (P1-04).

Before this change a tool bounded its output only if its author remembered to: six of them had a
20 000-character constant of their own, the rest (``list_dir``, ``grep``, ``extract``, every OpenAPI
connector's ancestor, a tool written next month) returned whatever they had. ``ToolRegistry.run`` is
the one door, so the floor lives there. Three tools cut and did not say how to read on; they now
take ``offset``. A subagent's answer was handed over whole and uncounted.

The risk that shaped the design is the fence: by the time a result reaches the registry the taint
layer may already have wrapped it in the data-fence markers, and a cut at a character count would eat
the closing marker, silently turning an untrusted page into text that reads as the agent's own.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.governance.ledger import TaintLedger
from chimera.governance.ledger_tool import ledger_registry
from chimera.governance.sanitize import FENCE_CLOSE, FENCE_OPEN, fence
from chimera.tools.base import Tool, clips_own_output
from chimera.tools.clip import CENTRAL_MAX_OUTPUT_CHARS, clip_central, clip_page
from chimera.tools.registry import ToolRegistry


class _Big(Tool):
    """Declares nothing about its output, which is the case the ceiling exists for."""

    name = "big"
    description = "returns a lot"
    parameters = {"type": "object", "properties": {}}

    def __init__(self, size: int = 100_000, *, untrusted: bool = False) -> None:
        self.size = size
        self.untrusted_output = untrusted

    def run(self, **kwargs: Any) -> str:
        return "x" * self.size


def _marker_follows_the_ceiling(text: str, total: int) -> bool:
    return text[CENTRAL_MAX_OUTPUT_CHARS:].lstrip("\n. ").startswith(f"[truncated, {total} chars total")


# --- the central ceiling ---------------------------------------------------------------------------


def test_a_tool_that_declares_nothing_is_cut_at_the_ceiling() -> None:
    registry = ToolRegistry()
    registry.register(_Big())

    out = registry.run("big")

    assert out.startswith("x" * CENTRAL_MAX_OUTPUT_CHARS)
    assert _marker_follows_the_ceiling(out, 100_000)
    assert len(out) <= CENTRAL_MAX_OUTPUT_CHARS + 80  # the ceiling, plus the marker and nothing else


def test_a_short_result_is_returned_as_it_is() -> None:
    registry = ToolRegistry()
    registry.register(_Big(CENTRAL_MAX_OUTPUT_CHARS))

    assert registry.run("big") == "x" * CENTRAL_MAX_OUTPUT_CHARS


def test_an_untrusted_result_keeps_its_fence_closed_through_the_taint_layer() -> None:
    registry = ToolRegistry()
    registry.register(_Big(untrusted=True))
    wrapped = ledger_registry(registry, TaintLedger())

    out = wrapped.run("big")

    assert out.startswith(FENCE_OPEN)
    assert out.endswith(FENCE_CLOSE), "the cut ate the closing marker"
    assert out.count(FENCE_OPEN) == 1 and out.count(FENCE_CLOSE) == 1
    inside = out[len(FENCE_OPEN) + 1 : -(len(FENCE_CLOSE) + 1)]
    assert inside.startswith("x" * CENTRAL_MAX_OUTPUT_CHARS)
    assert "[truncated, 100000 chars total" in inside, "the marker belongs inside the data region"


def test_a_fenced_failure_stays_a_failure_after_the_cut() -> None:
    class _Fails(_Big):
        def run(self, **kwargs: Any) -> str:
            return "error: " + "y" * 100_000

    registry = ToolRegistry()
    registry.register(_Fails(untrusted=True))

    out = ledger_registry(registry, TaintLedger()).run("big")

    assert out.endswith(FENCE_CLOSE)
    assert out.split(FENCE_OPEN)[0].startswith("error:")  # the loop still reads it as a call that failed


def test_clip_central_cuts_a_fence_in_its_middle_whatever_precedes_it() -> None:
    wrapped = "note before\n" + fence("z" * 50_000)

    out = clip_central(wrapped)

    assert out.startswith("note before\n" + FENCE_OPEN)
    assert out.endswith(FENCE_CLOSE)
    assert len(out) < 21_000


def test_a_refusal_keeps_its_type() -> None:
    from chimera.tools.base import Refusal, refusal

    class _Refuses(_Big):
        def run(self, **kwargs: Any) -> str:
            return refusal("no " * 20_000)

    registry = ToolRegistry()
    registry.register(_Refuses())

    assert isinstance(registry.run("big"), Refusal)


def test_the_span_records_that_the_output_was_clipped_and_how_big_it_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class _Span:
        def __enter__(self) -> _Span:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def set(self, **attrs: Any) -> None:
            seen.update(attrs)

    monkeypatch.setattr("chimera.obs.span", lambda *a, **k: _Span())
    registry = ToolRegistry()
    registry.register(_Big())

    registry.run("big")

    assert seen["tool.output_clipped"] is True
    assert seen["tool.output_raw_chars"] == 100_000


# --- the tools that bound themselves are left exactly as they were ------------------------------------


class _Sandbox:
    def __init__(self, output: str) -> None:
        self._output = output

    def run(self, command: str, *, timeout: int, cwd: Any = None, **_: Any) -> SimpleNamespace:
        return SimpleNamespace(output=self._output, exit_code=0, timed_out=False, adopted=None)

    def is_isolated(self) -> bool:
        return False


_FIFTY = "abcdefghij" * 5_000


def test_read_file_is_byte_for_byte_what_it_was(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("a\n" * 25_000, encoding="utf-8")
    from chimera.tools.files import ReadFileTool

    registry = ToolRegistry()
    registry.register(ReadFileTool(tmp_path))

    out = registry.run("read_file", path="big.txt")

    assert out == (
        "a\n" * 10_000
        + "\n... [truncated, lines 1–10000 of 25000 shown, 50000 chars from here; continue with start_line=10001]"
    )


def test_run_shell_is_byte_for_byte_what_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHIMERA_EXEC_OUTPUT_TAIL", raising=False)
    from chimera.tools.shell import RunShellTool

    tool = RunShellTool(sandbox=_Sandbox(_FIFTY), confirm=None)  # type: ignore[call-arg]
    registry = ToolRegistry()
    registry.register(tool)

    out = registry.run("run_shell", command="echo")

    assert out.endswith(_FIFTY[:20_000] + "\n... [truncated, 50000 chars total]") or (
        _FIFTY[:20_000] + "\n... [truncated, 50000 chars total]" in out
    )
    assert out.count("[truncated,") == 1, "cut once, by the tool, not a second time by the registry"


def test_code_interpreter_is_byte_for_byte_what_it_was() -> None:
    from chimera.tools.code import CodeInterpreterTool

    registry = ToolRegistry()
    registry.register(CodeInterpreterTool())

    out = registry.run("code_interpreter", code="print('x' * 49_999)")

    assert out == "x" * 20_000 + "\n... [truncated, 49999 chars total]"


def test_the_browser_page_text_is_byte_for_byte_what_it_was() -> None:
    from chimera.tools.browser import render_text

    assert render_text(_FIFTY) == fence(_FIFTY[:20_000] + "\n... [truncated, 50000 chars total]")


def test_an_mcp_result_under_the_default_cap_passes_whole_and_over_it_is_cut_as_before() -> None:
    from chimera.integrations.mcp_client import (
        DEFAULT_MAX_OUTPUT_CHARS,
        StdioMCPSession,
        _cap_output,
    )

    assert StdioMCPSession("unused").max_output_chars == DEFAULT_MAX_OUTPUT_CHARS == 60_000
    assert _cap_output("m" * 50_000, DEFAULT_MAX_OUTPUT_CHARS) == "m" * 50_000
    assert _cap_output("m" * 70_000, DEFAULT_MAX_OUTPUT_CHARS) == (
        "m" * 60_000 + "\n... [truncated, 70000 chars total; limit 60000 chars]"
    )


# --- every shipped tool is on one side of the line ------------------------------------------------------

#: Tools with a cap of their own, and so a marker of their own.
_BOUND_THEMSELVES = {
    "chimera.core.subagent.SubAgentTool",
    "chimera.integrations.mcp_client.MCPTool",
    "chimera.integrations.mcp_defer.McpCallTool",
    "chimera.integrations.openapi.RestApiTool",
    "chimera.integrations.openapi_store.ConnectorTool",
    "chimera.tools.browser.BrowserTool",
    "chimera.tools.code.CodeInterpreterTool",
    "chimera.tools.code.ExecuteCodeTool",
    "chimera.tools.defer.ToolCallTool",
    "chimera.tools.documents.ReadDocumentTool",
    "chimera.tools.files.ReadFileTool",
    "chimera.tools.http.HttpGetTool",
    "chimera.tools.research.YouTubeTranscriptTool",
    "chimera.tools.scrape.CrawlTool",
    "chimera.tools.scrape.ScrapeTool",
    "chimera.tools.shell.RunShellTool",
    "chimera.tools.ssh_files.RemoteReadFile",
}

#: Tools that declare nothing and are held to the ceiling in `ToolRegistry.run`. A new tool must be
#: added to ONE of the two sets, which is the moment somebody decides which it is.
_UNDER_THE_CEILING = {
    "chimera.api.works.WorkStatusTool",
    "chimera.api.works.WorkStopTool",
    "chimera.api.works.WorkUndoTool",
    "chimera.core.explorer.ExploreRepositoryTool",
    "chimera.core.research.ResearchWebTool",
    "chimera.integrations.mcp_defer.McpDescribeTool",
    "chimera.integrations.mcp_defer.McpListTool",
    "chimera.integrations.messaging.SendMessageTool",
    "chimera.skills.aliases.SkillView",
    "chimera.tools.ask_user.AskUserTool",
    "chimera.tools.builtin.EchoTool",
    "chimera.tools.calendar.CalendarEventsTool",
    "chimera.tools.chart.RenderChartTool",
    "chimera.tools.create_document.CreateDocumentTool",
    "chimera.tools.decide.DecideTool",
    "chimera.tools.defer.ToolDescribeTool",
    "chimera.tools.defer.ToolListTool",
    "chimera.tools.design.DesignTool",
    "chimera.tools.download.DownloadMediaTool",
    "chimera.tools.edit.ApplyPatchTool",
    "chimera.tools.edit.EditBatchTool",
    "chimera.tools.edit.EditFileTool",
    "chimera.tools.email.ReadEmailTool",
    "chimera.tools.email.SendEmailTool",
    "chimera.tools.files.ListDirTool",
    "chimera.tools.files.WriteFileTool",
    "chimera.tools.history.RecallHistoryTool",
    "chimera.tools.jobs.JobCancelTool",
    "chimera.tools.jobs.JobStatusTool",
    "chimera.tools.media.GenerateAudioTool",
    "chimera.tools.media.GenerateVideoTool",
    "chimera.tools.media.ImageGenTool",
    "chimera.tools.media.TextToSpeechTool",
    "chimera.tools.media.TranscribeAudioTool",
    "chimera.tools.preview.PreviewTool",
    "chimera.tools.pull_request.OpenPullRequestTool",
    "chimera.tools.report_defect.ReportDefectTool",
    "chimera.tools.research.ArxivSearchTool",
    "chimera.tools.schedule_once.ScheduleOnceTool",
    "chimera.tools.scrape.ExtractTool",
    "chimera.tools.scrape.MapTool",
    "chimera.tools.search.GlobTool",
    "chimera.tools.search.GrepTool",
    "chimera.tools.ssh_files.RefusedLocalFileTool",
    "chimera.tools.ssh_files.RemoteApplyPatch",
    "chimera.tools.ssh_files.RemoteEditBatch",
    "chimera.tools.ssh_files.RemoteEditFile",
    "chimera.tools.ssh_files.RemoteFileTool",
    "chimera.tools.ssh_files.RemoteGlob",
    "chimera.tools.ssh_files.RemoteGrep",
    "chimera.tools.ssh_files.RemoteListDir",
    "chimera.tools.ssh_files.RemoteWriteFile",
    "chimera.tools.task_suggestion.DismissTaskTool",
    "chimera.tools.task_suggestion.SuggestTaskTool",
    "chimera.tools.todo.TodoWriteTool",
    "chimera.tools.view_image.ViewImageTool",
    "chimera.tools.web.WebSearchTool",
}

_PACKAGES = (
    "chimera.tools",
    "chimera.integrations",
    "chimera.core",
    "chimera.governance",
    "chimera.skills",
    "chimera.api",
)


# eval probes imported by other tests are not shipped tools
_PACKAGE_PREFIXES = tuple(p + "." for p in _PACKAGES)


def _subclasses(cls: type) -> list[type]:
    found: list[type] = []
    for sub in cls.__subclasses__():
        found.append(sub)
        found.extend(_subclasses(sub))
    return found


def _shipped_tools() -> dict[str, type[Tool]]:
    for package in _PACKAGES:
        root = importlib.import_module(package)
        for info in pkgutil.walk_packages(root.__path__, package + "."):
            try:
                importlib.import_module(info.name)
            except ImportError:  # an optional extra that is not installed here
                continue
    shipped: dict[str, type[Tool]] = {}
    for cls in _subclasses(Tool):
        if inspect.isabstract(cls) or not cls.__module__.startswith(_PACKAGE_PREFIXES):
            continue
        if "inner" in inspect.signature(cls.__init__).parameters:
            continue  # a wrapper: it answers through the tool it wraps, which is on this list
        if cls.__module__.startswith("chimera.core.research") and cls.__name__ == "_Fenced":
            continue
        shipped[f"{cls.__module__}.{cls.__qualname__}"] = cls
    return shipped


def test_every_shipped_tool_either_bounds_its_own_output_or_is_under_the_ceiling() -> None:
    shipped = _shipped_tools()

    own = {name for name, cls in shipped.items() if cls.clips_own_output}
    central = set(shipped) - own

    assert own == _BOUND_THEMSELVES, "declare `clips_own_output = True` only with a cap of its own"
    assert central == _UNDER_THE_CEILING, (
        "a tool that is on neither list: say which it is. New: "
        f"{sorted(central - _UNDER_THE_CEILING)}; gone: {sorted(_UNDER_THE_CEILING - central)}"
    )


def test_a_wrapper_resolves_the_declaration_through_the_chain() -> None:
    inner = _Big()
    inner.clips_own_output = True
    wrapper = SimpleNamespace(inner=SimpleNamespace(inner=inner))

    assert clips_own_output(wrapper)
    assert not clips_own_output(SimpleNamespace(inner=_Big()))


# --- the cursor ------------------------------------------------------------------------------------------

_TEXT = "".join(f"{i:05d}|" for i in range(7_500))  # 45 000 characters, every position distinct


def _read_all(call: Any) -> str:
    pieces: list[str] = []
    offset = 0
    for _ in range(10):
        page = call(offset)
        marker = "\n... [truncated, "
        if marker not in page:
            pieces.append(page)
            return "".join(pieces)
        body, _, note = page.rpartition(marker)
        assert note.endswith("]") and "continue with offset=" in note
        pieces.append(body)
        offset = int(note.rsplit("offset=", 1)[1].rstrip("]"))
    raise AssertionError("never reached the last page")


def test_clip_page_pages_without_a_gap_or_a_repeat_and_the_last_page_has_no_marker() -> None:
    assert _read_all(lambda off: clip_page(_TEXT, 20_000, off)) == _TEXT
    assert "[truncated," not in clip_page(_TEXT, 20_000, 40_000)


def test_an_offset_past_the_end_is_an_error_not_an_empty_page() -> None:
    assert clip_page("abc", 10, 3).startswith("error: offset 3 is past the end (3 chars)")
    assert clip_page("abc", 10, 99).startswith("error: offset 99")
    assert clip_page("", 10, 0) == ""


class _Response:
    status_code = 200
    encoding = "utf-8"
    is_redirect = False
    headers: dict[str, str] = {}

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def iter_bytes(self) -> Any:
        yield _TEXT.encode("utf-8")


class _Client:
    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def stream(self, method: str, url: str) -> _Response:
        return _Response()


def test_http_get_reads_a_long_page_by_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from chimera.tools.http import HttpGetTool

    monkeypatch.setattr(httpx, "Client", _Client)
    monkeypatch.setattr("chimera.scrape.ssrf.check_url", lambda url: None)
    tool = HttpGetTool()

    def call(offset: int) -> str:
        out = tool.run(url="https://example.test/page", offset=offset)
        head, _, body = out.partition("\n")
        assert head == "[200] https://example.test/page"
        return body

    assert _read_all(call) == _TEXT
    assert tool.run(url="https://example.test/page", offset=45_001).startswith("error: offset 45001")
    assert tool.run(url="https://example.test/page", offset=-1).startswith("error: offset must be")


def test_scrape_reads_a_long_page_by_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.tools import scrape as scrape_module

    page = SimpleNamespace(
        source="http", url="https://example.test/p", title="", status=200, markdown=_TEXT, links=[]
    )
    monkeypatch.setattr(scrape_module, "fetch_page", lambda url, render="auto": page)
    tool = scrape_module.ScrapeTool()

    def call(offset: int) -> str:
        out = tool.run(url="https://example.test/p", offset=offset)
        assert out.startswith(FENCE_OPEN) and out.endswith(FENCE_CLOSE)
        inner = out[len(FENCE_OPEN) + 1 : -(len(FENCE_CLOSE) + 1)]
        return inner.split("\n\n", 1)[1]  # past the header, which is repeated on every page

    assert _read_all(call) == _TEXT
    assert tool.run(url="https://example.test/p", offset=99_999).startswith("error: offset 99999")


def test_read_document_reads_a_long_document_by_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.tools import documents as documents_module

    (tmp_path / "d.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(documents_module, "_markitdown_convert", lambda path: _TEXT)
    tool = documents_module.ReadDocumentTool(tmp_path)

    def call(offset: int) -> str:
        out = tool.run(path="d.pdf", offset=offset)
        assert out.startswith(FENCE_OPEN) and out.endswith(FENCE_CLOSE)
        return out[len(FENCE_OPEN) + 1 : -(len(FENCE_CLOSE) + 1)]

    assert _read_all(call) == _TEXT
    assert tool.run(path="d.pdf", offset=50_000).startswith("error: offset 50000")


def test_the_cursor_is_declared_in_the_schema_of_the_three_tools() -> None:
    from chimera.tools.documents import ReadDocumentTool
    from chimera.tools.http import HttpGetTool
    from chimera.tools.scrape import ScrapeTool

    for tool in (HttpGetTool(), ScrapeTool(), ReadDocumentTool()):
        assert tool.parameters["properties"]["offset"]["type"] == "integer"


# --- subagents: cut before the fence, counted per run -----------------------------------------------------


class _Answers:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    def complete(self, messages: list[Any], **kwargs: Any) -> Any:
        from chimera.providers import CompletionResult

        return CompletionResult(content=self.answer, model="fake")


def _spawner(tmp_path: Path, answer: str) -> Any:
    from chimera.core import SubAgentTool
    from chimera.tools import EchoTool

    registry = ToolRegistry()
    registry.register(EchoTool())
    return SubAgentTool(_Answers(answer), registry, workspace=tmp_path)


def test_a_subagent_answer_is_cut_inside_its_fence(tmp_path: Path) -> None:
    out = _spawner(tmp_path, "w" * 100_000).run(task="go")

    assert out.count(FENCE_OPEN) == 1 and out.endswith(FENCE_CLOSE)
    inside = out.partition(FENCE_OPEN + "\n")[2][: -(len(FENCE_CLOSE) + 1)]
    assert inside.startswith("w" * CENTRAL_MAX_OUTPUT_CHARS)
    assert "[truncated, 100000 chars total" in inside
    assert len(out) < CENTRAL_MAX_OUTPUT_CHARS + 600


def test_two_spawns_in_one_run_report_two_and_the_steplog_keeps_the_count(tmp_path: Path) -> None:
    from chimera.core.agent import OpenRun, _open_runs, _UsageTally

    tally = _UsageTally()
    runs = _open_runs()
    runs.append(OpenRun(tally, None))
    try:
        tool = _spawner(tmp_path, "ok")
        first = tool.run(task="a")
        second = tool.run(task="b")
    finally:
        runs.pop()

    assert "Spawns this run: 1." in first
    assert "Spawns this run: 2." in second
    assert tally.subagent_spawns == 2


def test_the_spawn_limit_is_off_by_default_and_refuses_when_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.config import get_settings
    from chimera.core.agent import OpenRun, _open_runs, _UsageTally
    from chimera.tools.base import is_refusal

    assert get_settings().subagent_max_spawns == 0
    monkeypatch.setattr(get_settings(), "subagent_max_spawns", 1)
    runs = _open_runs()
    runs.append(OpenRun(_UsageTally(), None))
    try:
        tool = _spawner(tmp_path, "ok")
        assert "Spawns this run: 1." in tool.run(task="a")
        assert is_refusal(tool.run(task="b"))
    finally:
        runs.pop()


def test_the_steplog_serialises_as_before_until_something_was_delegated() -> None:
    from chimera.core.steplog import StepLog

    log = StepLog()
    assert "subagent_spawns" not in log._summary_dict()


# --- MCP: the lower ceiling is an option, and the excess goes to a file --------------------------------------


def test_a_lowered_mcp_ceiling_keeps_the_whole_answer_in_a_file(tmp_path: Path) -> None:
    from chimera.integrations.mcp_client import _cap_output
    from chimera.integrations.mcp_payloads import PayloadSink

    sink = PayloadSink(root=tmp_path / "mcp-output", workspace=tmp_path, relative_to=tmp_path)
    text = "r" * 50_000

    out = _cap_output(text, 20_000, sink)

    assert out.startswith("r" * 20_000 + "\n... [truncated, 50000 chars total; limit 20000 chars; ")
    shown = out.rsplit("saved to ", 1)[1].rstrip("]")
    assert (tmp_path / shown).read_text(encoding="utf-8") == text


def test_the_mcp_default_cap_follows_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.config import get_settings
    from chimera.integrations.mcp_client import StdioMCPSession

    monkeypatch.setattr(get_settings(), "mcp_default_max_output_chars", 20_000)
    session = StdioMCPSession("unused")
    assert session.max_output_chars == 20_000 and session._spill_overflow
    explicit = StdioMCPSession("unused", max_output_chars=70_000)
    assert explicit.max_output_chars == 70_000 and not explicit._spill_overflow
