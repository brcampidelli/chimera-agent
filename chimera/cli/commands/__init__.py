"""The CLI's commands, one module per area (S30-70).

``chimera/cli/main.py`` held all of them until it reached 10,486 lines. Each module here registers
its commands on the shared ``app`` from :mod:`chimera.cli.commands._shared` when it is imported, and
:mod:`chimera.cli.main` imports them all and re-exports what they define.
"""
