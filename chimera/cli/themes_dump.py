"""Dump the command reference's grouping as JSON to stdout.

    python -m chimera.cli.themes_dump > chimera/_cli_themes.json

The sibling of :mod:`chimera.cli.schema_dump`, and committed the same way. The snapshot says which
commands exist; this says how the reference groups them, and the documentation site reads both out
of the installed product rather than keeping its own copy.

No version stamp, deliberately. The snapshot carries one because a reader needs to know which
release a flag list belongs to; a grouping is not something you can be out of date about in that
sense — it is either complete against the CLI or it is not, and `tests/` decides that on every
pull request.
"""

from __future__ import annotations

import json

from chimera.cli.themes import build


def main() -> None:
    print(json.dumps(build(), indent=2))


if __name__ == "__main__":
    main()
