"""Frozen backend entrypoint for the Chimera desktop (Tauri) app.

PyInstaller freezes this into the ``chimera-backend`` sidecar binary the Tauri shell launches. It is a
thin wrapper over the SAME ``chimera app`` CLI command the pip path uses, so the frozen app and
``pip install 'chimera-agent[desktop]'`` behave identically — one server implementation, no drift.

Tauri invokes it with a free port and a port file it then reads to learn the real URL::

    chimera-backend --no-open --port 0 --emit-port-file <path>

The wrapper just prepends the ``app`` subcommand so those args land on ``desktop_app`` — except
for ``chimera-backend service ...``, which is passed through (``chimera service start`` spawns it).

Once bound, the server it runs records how to start it again — ``~/.chimera/desktop-install.json``,
its executable, working folder, four shell paths, version and port — because ``sys.frozen`` is true
here and only here (``chimera/core/desktop_install.py``). That is how ``chimera service start`` in a
terminal brings up THIS backend, reading the keys from this install's ``.env``.
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

    original_argv = sys.argv
    sys.argv = cli_argv(original_argv)
    if original_argv[1:3] == ["service", "run"]:
        from chimera.cli.service_cmd import _run_service_process

        _run_service_process(app)
        return
    app()


def cli_argv(argv: list[str]) -> list[str]:
    """Tauri's argv lands on ``chimera app``; ``service ...`` (S1) is passed through as typed.

    ``chimera service start`` from a frozen build spawns this same binary as ``service run``, so
    prefixing ``app`` there would hand ``service`` to ``desktop_app`` as an unknown argument.
    """
    if len(argv) > 1 and argv[1] == "service":
        return list(argv)
    return [argv[0], "app", *argv[1:]]


if __name__ == "__main__":
    main()
