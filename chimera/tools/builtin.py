"""The native tool library shipped with Chimera.

These are the primitive, hand-written capabilities the agent loop and skills build
on. Higher-level, *learned* procedures live in :mod:`chimera.skills`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.tools.base import Tool
from chimera.tools.edit import ApplyPatchTool, EditFileTool
from chimera.tools.files import ListDirTool, ReadFileTool, WriteFileTool
from chimera.tools.http import HttpGetTool
from chimera.tools.registry import ToolRegistry
from chimera.tools.search import GlobTool, GrepTool
from chimera.tools.shell import RunShellTool
from chimera.tools.write_region import WriteRegion

if TYPE_CHECKING:
    from chimera.sandbox.confirm import HostExecConfirm


class _Unset:
    """Sentinel distinguishing "host_exec_confirm not passed" (resolve from settings) from an
    explicit ``None`` (caller opts out of the host-exec gate)."""


_UNSET = _Unset()


class EchoTool(Tool):
    """Return the provided text unchanged (useful for tests and demos)."""

    name = "echo"
    description = "Echo back the given text exactly."
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Text to echo."}},
        "required": ["text"],
    }

    def run(self, **kwargs: Any) -> str:
        return str(kwargs.get("text", ""))


def default_registry(
    workspace: Path | None = None,
    *,
    write_region: WriteRegion | None = None,
    host_exec_confirm: HostExecConfirm | None | _Unset = _UNSET,
) -> ToolRegistry:
    """Build a registry pre-populated with the built-in native tools.

    File and shell tools are rooted at ``workspace`` (default: current directory). When
    ``write_region`` is given, the file-writing tools refuse a write outside its declared globs
    (M18-3) — the capability boundary that blocks an injected instruction from rewriting an
    unrelated file.

    ``host_exec_confirm`` gates the shell/code tools before they run on the host. Left unset, it is
    resolved from settings + whether stdin is a TTY (see :func:`resolve_host_exec_confirm`): an
    interactive terminal confirms each host command, headless runs with a one-time warning. Pass
    ``None`` to force no gate (e.g. a server that must never block on stdin), or a custom callback.
    """
    from chimera.sandbox import get_sandbox
    from chimera.sandbox.confirm import resolve_host_exec_confirm

    confirm = resolve_host_exec_confirm() if isinstance(host_exec_confirm, _Unset) else host_exec_confirm

    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(ReadFileTool(workspace))
    registry.register(WriteFileTool(workspace, write_region=write_region))
    registry.register(EditFileTool(workspace, write_region=write_region))
    registry.register(ApplyPatchTool(workspace, write_region=write_region))
    registry.register(ListDirTool(workspace))
    registry.register(GrepTool(workspace))
    registry.register(GlobTool(workspace))
    registry.register(RunShellTool(workspace, get_sandbox(), confirm=confirm))
    registry.register(HttpGetTool())

    # Always-on reference tools (no credential needed).
    from chimera.tools.code import CodeInterpreterTool, ExecuteCodeTool
    from chimera.tools.documents import ReadDocumentTool
    from chimera.tools.research import ArxivSearchTool, YouTubeTranscriptTool

    registry.register(ExecuteCodeTool(workspace, get_sandbox(), confirm=confirm))
    registry.register(CodeInterpreterTool())
    registry.register(ReadDocumentTool(workspace))
    registry.register(ArxivSearchTool())
    registry.register(YouTubeTranscriptTool())
    from chimera.tools.download import DownloadMediaTool

    registry.register(DownloadMediaTool(workspace))
    from chimera.tools.chart import RenderChartTool

    registry.register(RenderChartTool(workspace))  # Vega-Lite spec -> HTML (dep-free) / PNG-SVG (viz-vega)

    # Web scraping + secure structured extraction (fetch->clean markdown; schema->JSON via quarantine)
    # + whole-site discovery (map/crawl, robots-aware).
    from chimera.tools.scrape import CrawlTool, ExtractTool, MapTool, ScrapeTool

    registry.register(ScrapeTool())
    registry.register(ExtractTool())
    registry.register(MapTool())
    registry.register(CrawlTool())

    # Key-gated optional tools light up when their credential is set.
    from chimera.config import get_settings

    settings = get_settings()
    if settings.tavily_api_key:
        from chimera.tools.web import WebSearchTool

        registry.register(WebSearchTool())
    import importlib.util

    if settings.key_pool("openai") or importlib.util.find_spec("diffusers") is not None:
        from chimera.tools.media import ImageGenTool

        registry.register(ImageGenTool())  # hosted (OpenAI key) or local (diffusers/imagegen-local)
    if settings.elevenlabs_api_key:
        from chimera.tools.media import TextToSpeechTool

        registry.register(TextToSpeechTool())
    # Speech-to-text lights up with local faster-whisper (the `stt` extra) OR an OpenAI key.
    import importlib.util

    if importlib.util.find_spec("faster_whisper") is not None or settings.key_pool("openai"):
        from chimera.tools.media import TranscribeAudioTool

        registry.register(TranscribeAudioTool(workspace))
    if settings.smtp_host and settings.smtp_user and settings.smtp_password:
        from chimera.tools.email import SendEmailTool

        registry.register(SendEmailTool())
    if settings.imap_host and settings.imap_user and settings.imap_password:
        from chimera.tools.email import ReadEmailTool

        registry.register(ReadEmailTool())
    if settings.calendar_ics_url:
        from chimera.tools.calendar import CalendarEventsTool

        registry.register(CalendarEventsTool())
    # The browser is a core capability now (playwright is a base dependency; the Chromium binary is
    # auto-downloaded on first use). The find_spec guard just keeps a broken install from crashing.
    import importlib.util

    if importlib.util.find_spec("playwright") is not None:
        from chimera.tools.browser import BrowserTool

        registry.register(BrowserTool(headless=settings.browser_headless))
    return registry
