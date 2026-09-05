"""An ignored dependency stops being proposed, which is also how its reason stops being checked.

TypeScript 7 cannot be installed in `apps/desktop`, and not because of our code. `openapi-typescript`
declares `peer typescript: "^5.x"`, so `npm ci` dies on ERESOLVE before a single file is type-checked
— which is what PR #329 was, under a title that read like a migration. Every published version up to
7.13.0, the one already in the lockfile, declares that same range.

So `.github/dependabot.yml` ignores the major, exactly as it already does for `mcp`: a PR that can
only ever be closed should not arrive every week.

The failure this file exists for is the one that comes *after* that decision. An ignore is silent by
construction — the bot stops proposing, so nobody is reminded the constraint exists, and the day
upstream widens its peer range is a day nothing happens. The ignore then outlives its reason and
holds the project a major version back for no remaining cause. That is the same shape as every other
defect this repo keeps finding: not a wrong answer, an absent question.

So the second test asserts the *reason*, not the decision. If `openapi-typescript` changes that
range at all — widened, narrowed, rewritten — the assertion fails and says what to do about it. Any
change there deserves a human look; a test that tried to decide on its own which new ranges are
acceptable would be re-implementing semver to avoid reading one line.

What this cannot show: whether TypeScript 7 would actually *build* this app once installable. The
peer conflict stops the install, so the type errors behind it — if there are any — have never been
seen. Lifting the ignore is the beginning of that question, not the end of it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import yaml

REPO = Path(__file__).resolve().parents[1]

#: The peer range that makes the ignore necessary. Read from the lockfile rather than the registry:
#: a test that reaches the network fails for reasons that have nothing to do with the claim.
BLOCKING_PEER_RANGE = "^5.x"


def _npm_ecosystem() -> dict[str, Any]:
    config = yaml.safe_load((REPO / ".github/dependabot.yml").read_text(encoding="utf-8"))
    npm = [u for u in config["updates"] if u["package-ecosystem"] == "npm"]
    assert len(npm) == 1, f"expected exactly one npm ecosystem, found {len(npm)}"
    return cast("dict[str, Any]", npm[0])


def test_typescript_majors_are_ignored() -> None:
    """Without this the unmergeable PR returns weekly, and weekly closes train rubber-stamping."""
    ignored = _npm_ecosystem().get("ignore", [])
    entries = [i for i in ignored if i.get("dependency-name") == "typescript"]

    assert entries, (
        "dependabot.yml no longer ignores typescript majors. If that was deliberate, the peer "
        "conflict must have been resolved — check that the other test in this file agrees."
    )
    assert "version-update:semver-major" in entries[0].get("update-types", []), (
        "the ignore must cover the MAJOR only: 5.x minors and patches are still installable and "
        "are the part that can be taken."
    )


def test_minors_are_still_proposed() -> None:
    """Ignoring the whole package would freeze it, and 5.x patches carry the security fixes."""
    entries = [
        i for i in _npm_ecosystem().get("ignore", []) if i.get("dependency-name") == "typescript"
    ]
    # Not an IndexError: with no entry at all there is nothing here to be too wide, and the test
    # above is the one that owns that case. Say so rather than crashing on `entries[0]`.
    assert entries, "no typescript ignore at all — see test_typescript_majors_are_ignored"
    types = entries[0].get("update-types", [])

    assert types, "an ignore with no update-types blocks EVERY version, including 5.x patches"
    assert "version-update:semver-minor" not in types
    assert "version-update:semver-patch" not in types


def test_the_reason_for_the_ignore_still_holds() -> None:
    """The ignore is silent, so this is the only thing that will notice upstream moving."""
    lock = json.loads((REPO / "apps/desktop/package-lock.json").read_text(encoding="utf-8"))
    nodes = {
        key: node
        for key, node in lock.get("packages", {}).items()
        if key.endswith("node_modules/openapi-typescript")
    }

    assert nodes, (
        "openapi-typescript is gone from the lockfile. It generates src/lib/api-schema.ts, which "
        "the CI drift gate compares against openapi.json — if it was replaced, the typescript "
        "ignore in dependabot.yml may have lost its reason and should be re-examined."
    )
    for key, node in nodes.items():
        peer = (node.get("peerDependencies") or {}).get("typescript")
        assert peer == BLOCKING_PEER_RANGE, (
            f"{key} now declares peer typescript {peer!r}, not {BLOCKING_PEER_RANGE!r}. That range "
            "is the entire reason .github/dependabot.yml ignores typescript majors. Read it: if it "
            "now admits 7.x, lift the ignore, let the bump land, and find out whether the app "
            "actually compiles — which nobody has ever seen, because the install never got that far."
        )
