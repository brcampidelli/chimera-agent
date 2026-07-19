"""Document ingestion — read a PDF/DOCX/PPTX/XLSX/HTML/CSV file as Markdown.

Chimera could read text files but not real documents. This wraps Microsoft's MarkItDown
(any-format -> Markdown, optimized for LLM consumption) behind an opt-in extra so the core
stays light. Absent the extra, the tool returns a one-line install hint instead of failing.

The conversion goes through :func:`_markitdown_convert`, a tiny seam that lazy-imports the
dependency — so the tool is unit-testable (truncation, missing-extra, bad-file) without the
package installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.governance.ledger_tool import fence
from chimera.governance.sanitize import sanitize_untrusted
from chimera.tools.base import Tool
from chimera.tools.workspace import resolve_in_workspace

_MAX_CHARS = 20_000
_INSTALL_HINT = (
    "error: reading documents needs an extra — install with: "
    "pip install 'chimera-agent[documents]'"
)


def _markitdown_convert(path: str) -> str:
    """Convert a document to Markdown. Raises ImportError if the extra is not installed."""
    from markitdown import MarkItDown  # lazy: only needed when the tool actually runs

    return str(MarkItDown().convert(path).text_content)


class _WorkspaceTool(Tool):
    """Base for tools bound to a workspace root."""

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()


class ReadDocumentTool(_WorkspaceTool):
    name = "read_document"
    # A document (PDF/DOCX/HTML/…) can carry a prompt injection exactly like a fetched web page —
    # the run() body already fences+sanitizes it for that reason. But fencing is cosmetic: without
    # this flag the taint ledger never learns the run touched untrusted content, so the downstream
    # gates (tool-narrowing, provenance on durable writes, pause-on-taint) stay dormant. Marking the
    # output untrusted routes it through the SAME provenance path as MCP/OpenAPI results
    # (ledger_tool.py honours `untrusted_output`), so a poisoned document taints the run like a
    # poisoned URL does. (Under `--taint`; off by default, like the rest of the ledger.)
    untrusted_output = True
    description = (
        "Read a document (PDF, DOCX, PPTX, XLSX, HTML, CSV, JSON, EPUB) from the workspace and "
        "return its text as Markdown. Use this for formats read_file cannot handle."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Document path relative to the workspace."}
        },
        "required": ["path"],
    }

    def run(self, **kwargs: Any) -> str:
        rel = str(kwargs["path"])
        path = resolve_in_workspace(self.workspace, rel)
        if not path.is_file():
            return f"error: file not found: {rel}"
        try:
            text = _markitdown_convert(str(path))
        except ImportError:
            return _INSTALL_HINT
        except Exception as exc:  # noqa: BLE001 — a bad/unsupported file is a tool error, not a crash
            return f"error: could not read {rel}: {exc}"
        if len(text) > _MAX_CHARS:
            text = text[:_MAX_CHARS] + f"\n... [truncated, {len(text)} chars total]"
        # A PDF/DOCX/HTML is untrusted external content — a document can carry a prompt injection
        # just like a web page. Defang control tokens and data-fence it so its text can't pose as
        # instructions, matching how the browser/fetch tools return page content.
        return fence(sanitize_untrusted(text))
