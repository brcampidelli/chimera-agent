"""Inspect a workspace image by attaching it to the conversation's next gateway message."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.tools.base import Tool
from chimera.tools.image_safety import MAX_IMAGE_BYTES, image_extension
from chimera.tools.image_url import markdown_image
from chimera.tools.svg_safety import MAX_SVG_BYTES, is_svg_document, sanitise_svg
from chimera.tools.workspace import resolve_in_workspace


class ViewImageTool(Tool):
    name = "view_image"
    description = "View an image in the workspace. The image is attached to the conversation so a vision-capable model can inspect it; the result includes a Markdown image line, which you should repeat in your final answer so the owner can see it."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Image path relative to the workspace."}},
        "required": ["path"],
    }
    #: The bytes and the file name are workspace content the owner did not write this turn, so the
    #: result is treated like a fetched page: fenced, and it taints the run (as `media.py` audio does).
    untrusted_output = True

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    def run(self, **kwargs: Any) -> str:
        from chimera.api.bridge_routes import is_secret_file

        try:
            path = resolve_in_workspace(self.workspace, str(kwargs["path"]))
        except ValueError:
            return "error: image is outside the workspace"
        relative = path.relative_to(self.workspace).as_posix()
        if is_secret_file(relative):
            return "error: this file holds credentials and cannot be viewed"
        try:
            if not path.is_file():
                raise ValueError("missing")
            with path.open("rb") as handle:
                # One bounded read: the bytes validated here are the bytes the gateway re-checks.
                data = handle.read(MAX_IMAGE_BYTES + 1)
        except OSError:
            return "error: image is missing, too large, or not a supported image"
        try:
            image_extension(data)
        except ValueError:
            # Not a raster. An SVG is described, never attached: the model takes no SVG input.
            if is_svg_document(data) and len(data) <= MAX_SVG_BYTES:
                return self._svg_summary(relative, data)
            return "error: image is missing, too large, or not a supported image"
        from chimera.providers.listing import known_vision

        model = str(kwargs.get("model", ""))
        if model and known_vision(model) is False:
            return "This model cannot view images because it does not support vision."
        # The name is quoted as JSON, so a newline or an instruction written into a file name stays
        # inside one string the model reads as data, not as part of the result's own text.
        quoted = json.dumps(relative, ensure_ascii=False)
        # Registry result hook consumes this marker and the next message adds a local image ref.
        return (
            f"Viewed image (file name is quoted data): {quoted}\n"
            f"{markdown_image(self.workspace, relative, 'Viewed image')}"
        )

    def _svg_summary(self, relative: str, data: bytes) -> str:
        """What the model can know about an SVG it cannot look at: its size and what it draws with.

        The counts and the viewBox come from the validated rebuild, not the file, so the summary
        describes what would actually be served. The image line is there for the owner to see.
        """
        try:
            clean, report = sanitise_svg(data)
        except ValueError as exc:
            return f"error: SVG image was refused by validation ({exc})"
        elements = ", ".join(f"{count} {name}" for name, count in sorted(report.element_counts.items())) or "none"
        view_box = report.view_box or "not set"
        quoted = json.dumps(relative, ensure_ascii=False)
        return (
            f"SVG image (file name is quoted data): {quoted}\n"
            "The model cannot view SVG directly, so the picture was not attached. "
            f"Its validated source is {len(clean)} bytes; viewBox: {view_box}; elements: {elements}.\n"
            f"{markdown_image(self.workspace, relative, 'SVG image')}"
        )
