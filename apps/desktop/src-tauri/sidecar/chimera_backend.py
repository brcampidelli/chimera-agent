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
    # `--version` is answered HERE rather than passed through, and it is the only argument that is.
    # Everything else is prepended with `app`, so `--version` would land on that subcommand, which
    # does not have it — the flag would be an error instead of an answer.
    #
    # It exists so the release can ASK the frozen binary what version it is. That question had no
    # answer before, and the gap shipped: the freeze did not carry the package metadata, so
    # `importlib.metadata.version("chimera-agent")` failed inside the bundle, `__version__` fell back
    # to "0.0.0+source", and every installed app reported that in its footer — which also silenced
    # the in-app update notice, since an unparseable version can never be compared. Nothing detected
    # it because nothing could run the artefact and ask. Now the release pipeline does.
    if "--version" in sys.argv[1:]:
        import chimera

        print(chimera.__version__)
        return

    from chimera.cli.main import app

    sys.argv = [sys.argv[0], "app", *sys.argv[1:]]
    app()


if __name__ == "__main__":
    main()
