"""Long-lived child processes.

Chimera already spawns children in three places, and each one is a *transaction*: run a command,
read its output, reap it (:mod:`chimera.sandbox.local`, :mod:`chimera.api.exec_stream`,
:mod:`chimera.core.verify`). An external coding agent is not that shape. It starts, stays up across
many turns, and talks in both directions — so it needs a lifetime, a protocol frame, and somebody
whose job is making sure it dies when we do.

This package is that somebody. It knows nothing about ACP or about any protocol above the framing;
what it owns is the process.
"""

from chimera.proc.stdio import (
    StdioProcess,
    kill_tree,
    reap_all,
    resolve_program,
    track,
    unsafe_windows_argument,
    untrack,
)

__all__ = [
    "StdioProcess",
    "kill_tree",
    "reap_all",
    "resolve_program",
    "track",
    "unsafe_windows_argument",
    "untrack",
]
