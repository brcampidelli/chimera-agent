"""The native tool library shipped with Chimera.

These are the primitive, hand-written capabilities the agent loop and skills build
on. Higher-level, *learned* procedures live in :mod:`chimera.skills`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.tools.base import Tool
from chimera.tools.files import ListDirTool, ReadFileTool, WriteFileTool
from chimera.tools.http import HttpGetTool
from chimera.tools.registry import ToolRegistry
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
    registry.register(ListDirTool(workspace))
    registry.register(RunShellTool(workspace, get_sandbox()))
    registry.register(HttpGetTool())

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
    return registry
