"""Frozen backend entrypoint for the Chimera desktop (Tauri) app.

PyInstaller freezes this into the ``chimera-backend`` sidecar binary the Tauri shell launches. It is a
thin wrapper over the SAME ``chimera app`` CLI command the pip path uses, so the frozen app and
``pip install 'chimera-agent[desktop]'`` behave identically — one server implementation, no drift.

Tauri invokes it with a free port and a port file it then reads to learn the real URL::

    chimera-backend --no-open --port 0 --emit-port-file <path>

The wrapper just prepends the ``app`` subcommand so those args land on ``desktop_app``.
"""

from __future__ import annotations

import sys


def main() -> None:
    """Run the real ``chimera app`` command with Tauri's argv (``--no-open``/``--port``/``--emit-port-file``)."""
    from chimera.cli.main import app

    sys.argv = [sys.argv[0], "app", *sys.argv[1:]]
    app()


if __name__ == "__main__":
    main()
