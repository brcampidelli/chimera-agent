"""Every unattended surface goes through the profile — enforced by the build, not by discipline.

Five surfaces ran with no governance at all: `serve`, the cron dispatch, the MCP server, the A2A
endpoint and the messaging adapters each built `default_registry(workspace)` raw. That was not a
decision anyone made; it is what happens when the guarantee lives in a convention and the convention
lives in whoever last edited the file.

So the last test here parses `chimera/cli/main.py` and **fails the build** if any surface constructs
a bare registry again. A grep would be the obvious way and the wrong one: it cannot tell an
assembled registry from the string appearing in a docstring or a comment about the fix.

The rest is the middle state. `observe` exists because going straight to enforcement on a schedule
that watches real money is how a silent failure gets discovered in production rather than in a
report — with narrowing on and no approver, a job that reads a feed cannot write for the rest of its
run, and the refusal arrives as an ordinary observation string the agent reads and moves past.
"""

from __future__ import annotations

import ast
import pathlib
import textwrap
from typing import Any

import pytest

from chimera.config import Settings
from chimera.governance.ledger import TaintLedger
from chimera.governance.profile import governed_profile
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry

MAIN = pathlib.Path(__file__).resolve().parents[1] / "chimera" / "cli" / "main.py"


class _Writer(Tool):
    name = "write_file"
    description = "writes"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.calls = 0

    def run(self, **kwargs: Any) -> str:
        self.calls += 1
        return "wrote"


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_Writer())
    return registry


def _settings(tmp_path: pathlib.Path, **kw: Any) -> Settings:
    return Settings(CHIMERA_HOME=str(tmp_path), **kw)  # type: ignore[arg-type]


# --- the three modes ----------------------------------------------------------------------------


def test_off_is_a_no_op(tmp_path: pathlib.Path) -> None:
    """The default has to change nothing. Governance arriving through an upgrade would alter what a
    running deployment is allowed to do, which is not a thing an upgrade may decide."""
    raw = _registry()

    wrapped, approvals = governed_profile(raw, settings=_settings(tmp_path), home=tmp_path)

    assert wrapped is raw
    assert not approvals.granted and not approvals.refused


def test_observe_refuses_nothing_and_counts_everything(tmp_path: pathlib.Path) -> None:
    """The whole reason the middle state exists.

    Every call that reaches an approver is one enforcement WOULD have refused. So the count after a
    window in observe mode is exactly the price of turning it on — measured on the real schedule,
    rather than guessed from the corpus.
    """
    registry, approvals = governed_profile(
        _registry(), settings=_settings(tmp_path), home=tmp_path, mode="observe"
    )
    tool = registry.get("write_file")
    # Make the run tainted the way a real job does: read something external first.
    for wrapper in (tool, getattr(tool, "inner", None)):
        ledger = getattr(wrapper, "ledger", None)
        if isinstance(ledger, TaintLedger):
            ledger.record_fetch("news-feed", content="the market moved")
            break

    out = tool.run(path="report.md", content="today's summary")

    assert "needs review" not in out, "observe mode refused something"
    assert approvals.granted, "observe mode refused nothing AND counted nothing — it saw nothing"


def test_an_unknown_mode_is_off_rather_than_a_guess(tmp_path: pathlib.Path) -> None:
    # A typo in an env var must not silently enable or silently disable something stricter than
    # intended. Off is the state that changes nothing.
    raw = _registry()

    wrapped, _ = governed_profile(raw, settings=_settings(tmp_path), home=tmp_path, mode="enfroce")

    assert wrapped is raw


def test_enforce_actually_wraps(tmp_path: pathlib.Path) -> None:
    registry, _ = governed_profile(
        _registry(), settings=_settings(tmp_path), home=tmp_path, mode="enforce"
    )

    assert registry.get("write_file") is not None
    assert type(registry.get("write_file")).__name__ != "_Writer", "nothing was wrapped"


# --- the build gate -----------------------------------------------------------------------------


#: The entry points that run with nobody watching. Named explicitly rather than "everything",
#: because the distinction is the point: `run`, `chat` and `solve` are attended — a person is at the
#: terminal, sees the tool calls, and can stop them — and `solve` additionally assembles its own
#: stack with options this profile does not model (its own guard/taint flags, the late-bound
#: subagent). Governing those by fiat here would be a refactor pretending to be a safety gate.
UNATTENDED = ("serve", "_start_cron_daemon", "_serve_mcp", "_build_a2a", "_serve_platform")


def _bare_registry_calls(tree: ast.Module, *, within: tuple[str, ...] = UNATTENDED) -> list[int]:
    """Line numbers where an UNATTENDED surface builds `default_registry(...)` outside the profile.

    An AST walk rather than a grep, because a grep cannot tell an assembled registry from the same
    words inside a docstring — and this test exists precisely to survive the next person who writes
    a comment about it. Nested functions count: `serve` builds its registry inside a `factory()`
    closure, which is exactly where it went missing.

    A call is legitimate when it is an argument to `governed_profile`.
    """
    scopes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in within
    ]
    governed: set[int] = set()
    for scope in scopes:
        for node in ast.walk(scope):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "governed_profile":
                for arg in node.args:
                    for inner in ast.walk(arg):
                        if isinstance(inner, ast.Call):
                            governed.add(id(inner))
    bare = []
    for scope in scopes:
        for node in ast.walk(scope):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "default_registry"
                and id(node) not in governed
            ):
                bare.append(node.lineno)
    return sorted(bare)


def test_no_unattended_surface_builds_a_registry_outside_the_profile() -> None:
    """The test that makes the guarantee structural.

    Five surfaces lost their governance not through a decision but through absence: each was written
    at a different time and none of them called the thing the others called. A convention cannot fix
    that. A build that fails can.
    """
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))

    bare = _bare_registry_calls(tree)

    assert not bare, (
        "an unattended surface assembles tools without governed_profile, at "
        f"chimera/cli/main.py:{bare} — every unattended entry point has to go through the same "
        "profile, or the deployment's allowlist and taint ledger are true only where someone "
        "remembered"
    )


def test_the_gate_would_catch_a_regression() -> None:
    """Proof the check is not vacuous: the same walk over a surface that DOES build one bare must
    find it, including from inside a nested factory. Without this, an AST bug makes the gate pass
    forever and the guarantee quietly stops existing."""
    source = textwrap.dedent(
        """
        def serve():
            def factory():
                registry = default_registry(ws)
        """
    )

    assert _bare_registry_calls(ast.parse(source), within=("serve",)) == [4]


def test_the_gate_ignores_the_attended_commands() -> None:
    # Otherwise it would demand a refactor of `solve` under the banner of a security fix — and a
    # gate that fires on things it was not built to defend gets weakened until it fires on nothing.
    source = textwrap.dedent(
        """
        def run():
            registry = default_registry(ws)
        """
    )

    assert _bare_registry_calls(ast.parse(source)) == []


@pytest.mark.parametrize("surface", ["serve", "cron", "mcp", "a2a", "platform"])
def test_each_named_surface_is_declared(surface: str) -> None:
    # The `surface=` label is what makes an audit line answerable ("which entry point was that?").
    # Cheap to drop while refactoring, and invisible once dropped.
    body = MAIN.read_text(encoding="utf-8")

    assert f'surface="{surface}' in body or f'surface=f"{surface}' in body
