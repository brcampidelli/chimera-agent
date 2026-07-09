"""The native tool library shipped with Chimera.

These are the primitive, hand-written capabilities the agent loop and skills build
on. Higher-level, *learned* procedures live in :mod:`chimera.skills`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.tools.base import Tool
from chimera.tools.edit import ApplyPatchTool, EditFileTool
from chimera.tools.files import ListDirTool, ReadFileTool, WriteFileTool
from chimera.tools.http import HttpGetTool
from chimera.tools.registry import ToolRegistry
from chimera.tools.search import GlobTool, GrepTool
from chimera.tools.shell import RunShellTool


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


def default_registry(workspace: Path | None = None) -> ToolRegistry:
    """Build a registry pre-populated with the built-in native tools.

    File and shell tools are rooted at ``workspace`` (default: current directory).
    """
    from chimera.sandbox import get_sandbox

    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(ReadFileTool(workspace))
    registry.register(WriteFileTool(workspace))
    registry.register(EditFileTool(workspace))
    registry.register(ApplyPatchTool(workspace))
    registry.register(ListDirTool(workspace))
    registry.register(GrepTool(workspace))
    registry.register(GlobTool(workspace))
    registry.register(RunShellTool(workspace, get_sandbox()))
    registry.register(HttpGetTool())

    # Always-on reference tools (no credential needed).
    from chimera.tools.code import CodeInterpreterTool, ExecuteCodeTool
    from chimera.tools.documents import ReadDocumentTool
    from chimera.tools.research import ArxivSearchTool, YouTubeTranscriptTool

    registry.register(ExecuteCodeTool(workspace, get_sandbox()))
    registry.register(CodeInterpreterTool())
    registry.register(ReadDocumentTool(workspace))
    registry.register(ArxivSearchTool())
    registry.register(YouTubeTranscriptTool())

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
    if settings.key_pool("openai"):
        from chimera.tools.media import ImageGenTool

        registry.register(ImageGenTool())
    if settings.elevenlabs_api_key:
        from chimera.tools.media import TextToSpeechTool

        registry.register(TextToSpeechTool())
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
