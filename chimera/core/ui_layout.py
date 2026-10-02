"""The desktop's screen layout, kept by the server so it survives a reinstall.

Phase 6 of the dynamic screen the owner approved on 2026-09-29, which asked for the layout (what is
hidden, minimised, moved, how wide) to live where the project list already lives, rather than only in
the webview's storage, which a reinstall or a cleared WebView2 profile wipes.

The server keeps it as an opaque JSON object. It does not validate the shape and should not: the client
owns the layout model and reads anything it does not recognise as the default (``lib/layout/store.ts``),
so a check here would be a second definition of the model that drifts from the first. What the server
does own is that the file is an object, that it is small, and that a write is never half-done.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

#: Far above a real layout (a few kilobytes) and far below anything that would hurt to hold in memory.
MAX_BYTES = 64_000


class UiLayoutStore:
    """One JSON object in one file, read fresh each time and replaced atomically."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def read(self) -> dict[str, Any] | None:
        """The stored layout, or None when there is none or what is there is not a JSON object.

        A layout is replaceable by the machine (the client falls back to its default), unlike a project
        list a person typed, so an unreadable file is simply not an answer rather than being moved aside.
        """
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
        return raw if isinstance(raw, dict) else None

    def write(self, value: object) -> None:
        """Replace the stored layout. Refuses anything but an object, and anything over ``MAX_BYTES``."""
        if not isinstance(value, dict):
            raise ValueError("a layout is a JSON object")
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise OverflowError(f"a layout is at most {MAX_BYTES} bytes")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            # Atomic on the same volume: a crash mid-write leaves the old layout, never half a new one.
            os.replace(tmp, self.path)
