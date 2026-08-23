"""The board's built-in lanes, named in two languages, kept from drifting apart.

The Tasks screen warns "no agent has this id" when a card's lane is not in the agent registry. Its
lane field starts on ``solve``, and ``solve`` is not an agent — it is a lane the server registers
itself, alongside ``crew``. So the warning fired on the form's own untouched default.

It was invisible for the one reason that makes a bug live long: the check also required
``known.length > 0``, so a fresh install with no agents stayed quiet. Registering your first agent
is what made the app start complaining about the lane that had been working all along.

The frontend now carries a copy of the built-in names, because it has no endpoint that reports them
and inventing one for two strings is worse than a copy that cannot drift. This is what stops it
drifting: the server's dict literal is the source of truth, read here by parsing rather than by
importing — ``build_api_app`` wants settings, a workspace and a guard, and none of that should be
constructed to answer a question about two string keys.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _server_lanes() -> set[str]:
    """The keys the server adds to ``runners`` beyond the registered agents.

    Parsed, not grepped. An earlier guard in this repo searched source text for a name and found it
    inside a comment that merely mentioned it — four times over, in four different files. A dict
    literal in the AST cannot be a sentence about a dict literal.
    """
    tree = ast.parse((ROOT / "chimera" / "api" / "features.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        # The runners dict is the one whose string keys are all lane names built from a *Lane call.
        values = [
            v for k, v in zip(node.keys, node.values, strict=True)
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        ]
        if not keys or len(keys) != len(values):
            continue
        if all(
            isinstance(v, ast.Call)
            and isinstance(v.func, ast.Name)
            and v.func.id.endswith("Lane")
            for v in values
        ):
            return set(keys)
    raise AssertionError("no lane-runner dict found in features.py — did the shape change?")


def _frontend_lanes() -> set[str]:
    src = (ROOT / "apps" / "desktop" / "src" / "components" / "Tasks.tsx").read_text(encoding="utf-8")
    m = re.search(r"export const BUILT_IN_LANES = \[([^\]]*)\]", src)
    assert m, "BUILT_IN_LANES is gone from Tasks.tsx"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def test_the_server_still_registers_lanes_of_its_own() -> None:
    # If this ever becomes empty the whole guard passes vacuously, which is how a check quietly
    # stops checking. Assert the premise before comparing against it.
    assert _server_lanes(), "the server registers no built-in lanes; the warning logic needs rethinking"


def test_the_screen_knows_every_lane_the_server_builds_in() -> None:
    missing = _server_lanes() - _frontend_lanes()
    assert not missing, f"the Tasks screen will warn that these always-present lanes do not exist: {missing}"


def test_the_screen_claims_no_lane_the_server_does_not_build_in() -> None:
    """The other direction, and not symmetry for its own sake.

    A stale name here silences the warning for a lane that really is missing — the exact failure the
    warning exists to prevent, reintroduced by the fix for its opposite.
    """
    extra = _frontend_lanes() - _server_lanes()
    assert not extra, f"these are not built-in lanes; a card filed under them would fail silently: {extra}"
