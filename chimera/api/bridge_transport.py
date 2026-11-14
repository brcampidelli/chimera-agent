"""The stdlib HTTP transport to the running app's bridge, and the sentence for 'nobody home'.

These three names lived in :mod:`chimera.server.desktop_mcp`, and ``chimera code`` imported them
from there at module top. Importing ``chimera.server.<anything>`` executes ``chimera/server/__init__``,
which imports the Discord adapter, the gateway and the agent core: so ``chimera --help`` paid for the
whole server stack to get one ``urlopen`` wrapper. They sit here, in a package whose ``__init__`` is
lazy and with nothing but the standard library, and ``desktop_mcp`` re-exports them so every existing
import and patch point keeps working.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

NOT_RUNNING = (
    "Chimera desktop is not running, or 'Allow Claude to operate this app' is off in Settings."
)

#: (method, url, token, body, timeout) -> (status, parsed body). ``status`` None = unreachable.
HttpCall = Callable[[str, str, str, Any, float], tuple[int | None, Any]]


def urllib_call(
    method: str, url: str, token: str, body: Any, timeout: float
) -> tuple[int | None, Any]:
    """The real transport: stdlib only, so this command needs nothing the core does not have."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # loopback URL from our own file
            raw = response.read()
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), int(exc.code)
    except (urllib.error.URLError, OSError, TimeoutError):
        return None, None
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, raw.decode("utf-8", errors="replace")
