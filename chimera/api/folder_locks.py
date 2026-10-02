"""One writer per folder, for coding turns and autonomous runs alike.

A turn and a run both snapshot a folder, edit it, check it and may put it back. Two of them in one
folder at once left a snapshot, a verification and an undo each describing a mix of both, and
undoing one reverted the other's edits (R9 of the review of several conversations at once,
2026-09-30). Turns took this lock from the start; runs did not, so a second window, the MCP bridge or
any other client could start a run beside a turn in the same folder. One object, made once per app
and handed to both, so the two routes cannot disagree about which folder is which.

Different folders never wait on each other. A folder is named by its resolved path, case-folded
where the file system folds case, so `C:\\Code\\App` and `c:\\code\\app\\` are one folder.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path


class FolderLocks:
    """One lock per folder, made the first time the folder is asked for."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def lock(self, folder: Path) -> threading.Lock:
        key = os.path.normcase(str(Path(folder).resolve()))
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())
