"""Build workspace-image Markdown URLs understood by the desktop renderer."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode


def workspace_image_url(workspace: Path, relative_path: str) -> str:
    """Return the authenticated app's image endpoint URL with URLSearchParams-compatible encoding."""
    root = workspace.resolve()
    image = (root / relative_path).resolve()
    relative = image.relative_to(root).as_posix()
    query = urlencode((("path", relative), ("workspace", str(root))))
    # Match WHATWG URLSearchParams, which escapes these characters that urllib leaves literal.
    query = query.replace("~", "%7E").replace("!", "%21").replace("'", "%27")
    query = query.replace("(", "%28").replace(")", "%29").replace("%2A", "*")
    return f"/api/fs/image?{query}"


def markdown_image(workspace: Path, relative_path: str, description: str) -> str:
    """Render one image citation using the workspace endpoint the app already understands."""
    return f"![{description}]({workspace_image_url(workspace, relative_path)})"
