"""Terminal UI: the full-screen app that ``chimera tui`` opens (see :mod:`chimera.tui.app`).

Without a terminal on stdin and stdout the app refuses to start (``NoTerminalError``) and the command
exits with status 1 instead of drawing into a pipe and waiting."""
