"""Build a plain-dict maturity snapshot + write the shipped fallback JSON.

:func:`build_snapshot` runs :func:`chimera.eval.maturity.score_repo` against a tests dir and flattens
the :class:`~chimera.eval.maturity.Scorecard` into a JSON-safe dict (the exact shape the desktop API
returns). It is honest by construction: every number is derived from the real test-file evidence glob;
nothing is invented.

Running ``python -m chimera.eval.maturity_snapshot`` writes ``chimera/_maturity_snapshot.json`` from
the repo's own ``tests/`` dir. That file is the SHIPPED FALLBACK: the ``tests/`` dir is not packaged in
the pip wheel, so a pip-installed ``chimera app`` reads this snapshot instead of globbing an empty dir
(which would misleadingly report everything Alpha 0/N). The write is byte-stable (sorted keys, 2-space
indent, trailing newline) so regenerating it produces a clean diff.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera import __version__
from chimera.eval.maturity import Scorecard, score_repo


def _flatten(card: Scorecard) -> dict[str, Any]:
    """Flatten a Scorecard into a JSON-safe dict (same shape the API returns)."""
    weak = card.weakest()
    return {
        "proven": card.proven,
        "total": card.total,
        "ratio": card.ratio,
        "level": card.level,
        "surfaces": [
            {
                "name": s.name,
                "proven": s.proven,
                "total": s.total,
                "ratio": s.ratio,
                "level": s.level,
                "missing": list(s.missing),
            }
            for s in card.surfaces
        ],
        "weakest": {"name": weak.name, "ratio": weak.ratio} if weak is not None else None,
        "generated_for": __version__,
    }


def build_snapshot(tests_dir: Path) -> dict[str, Any]:
    """Score ``tests_dir`` against the taxonomy and return the flattened snapshot dict."""
    return _flatten(score_repo(tests_dir))


def snapshot_path() -> Path:
    """The shipped fallback location: ``chimera/_maturity_snapshot.json`` (inside the package)."""
    return Path(__file__).resolve().parent.parent / "_maturity_snapshot.json"


def main() -> None:
    """Write the shipped fallback from the repo's own ``tests/`` dir, byte-stably."""
    snapshot = build_snapshot(Path("tests"))
    out = snapshot_path()
    # Force LF (newline="\n") so the file is byte-identical whether generated on Windows or Linux/CI —
    # otherwise the Windows default would write CRLF and a Linux regeneration would show a spurious diff.
    out.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {out} ({snapshot['proven']}/{snapshot['total']} proven — {snapshot['level']})")


if __name__ == "__main__":
    main()
