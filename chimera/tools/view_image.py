"""Inspect a workspace image by attaching it to the conversation's next gateway message."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.tools.base import Tool
from chimera.tools.image_safety import MAX_IMAGE_BYTES, image_extension
from chimera.tools.image_url import markdown_image
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
            image_extension(data)
        except (OSError, ValueError):
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
