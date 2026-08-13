"""Diagnostics from a real language server.

`ruff server` and nothing else, for now. It is already a dev-dependency here, it is one binary with
no Node runtime behind it, and it answers with the same diagnostics CI enforces — which is what
stops a squiggle in the editor from being something the build does not care about.

Two decisions worth reading before extending this:

* :mod:`chimera.lsp.positions` — the protocol counts columns in UTF-16 code units and Python counts
  code points. They agree for ASCII, which is why getting this wrong passes every test written
  against English source and puts every diagnostic on the wrong column in the first file with an
  emoji in a comment.
* :mod:`chimera.lsp.transport` — `Content-Length` counts BYTES, so the stream is binary. The ACP
  pump reads lines of text and cannot be reused; the process lifetime and the exit-time reaper are
  shared with it rather than copied.
"""

from chimera.lsp.client import Diagnostic, RuffClient, path_to_uri, uri_to_path
from chimera.lsp.positions import from_utf16_column, offset_of, position_of, to_utf16_column
from chimera.lsp.transport import LspTransport

__all__ = [
    "Diagnostic",
    "LspTransport",
    "RuffClient",
    "from_utf16_column",
    "offset_of",
    "path_to_uri",
    "position_of",
    "to_utf16_column",
    "uri_to_path",
]
