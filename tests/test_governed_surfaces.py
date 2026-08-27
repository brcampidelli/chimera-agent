"""Every unattended surface goes through the profile — enforced by the build, not by discipline.

Five surfaces ran with no governance at all: `serve`, the cron dispatch, the MCP server, the A2A
endpoint and the messaging adapters each built `default_registry(workspace)` raw. That was not a
decision anyone made; it is what happens when the guarantee lives in a convention and the convention
lives in whoever last edited the file.

So the last test here **fails the build** if a surface constructs a bare registry again. A grep would
be the obvious way and the wrong one: it cannot tell an assembled registry from the string appearing
in a docstring or a comment about the fix.

**That gate had a blind spot, and it was found the expensive way — by an audit, not by the build.**
It parsed one file and listed the five surface names inside it, so a sixth unattended surface written
somewhere else was not "failing"; it was invisible. `chimera/server/manager.py` — the Discord bot
started from the desktop app's Messaging toggle, the same bot `serve --discord` runs — built its
registry raw for three weeks after the sweep that was supposed to have covered it.

The shape of the fix matters more than the fix. A list of surfaces to CHECK fails open: forget to add
one and it is silently unguarded. So it is inverted here — the walk covers every module in the
package, and what is listed is the set of sites allowed to build a registry ungoverned, each with a
written reason. A new surface anywhere now fails the build until someone either governs it or says
in one line why it does not need to be.

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

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "chimera"


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


#: Sites allowed to build a registry WITHOUT the profile, each with the reason.
#:
#: Keyed by ``"<module path>:<qualified function>"``. Listing the exemptions rather than the
#: obligations is the whole design: the previous version listed five surfaces to check, so the sixth
#: — written in another file three weeks later — was not caught, it was never looked at. Here the
#: default for a new site is FAIL, and the cost of an exemption is one sentence.
#:
#: Two kinds of reason are acceptable and they are not the same. "A person is watching" means the
#: tool calls scroll past someone who can hit Ctrl-C. "It assembles its own" means the surface builds
#: an equivalent stack with options this profile does not model — a weaker claim, and every entry
#: making it is a candidate for consolidation rather than a settled answer.
EXEMPT: dict[str, str] = {
    # --- a person is at the terminal, watching the tool calls go by ---
    "chimera/cli/main.py:agent": "attended: one-shot at the terminal",
    "chimera/cli/main.py:chat": "attended: interactive REPL",
    "chimera/cli/main.py:assist": "attended: interactive",
    "chimera/cli/main.py:tui": "attended: interactive terminal UI",
    # --- assembles an equivalent stack from its own flags ---
    "chimera/cli/main.py:solve._run_solve": "own guard/taint/write-region flags + late-bound subagent",
    "chimera/cli/main.py:solve_batch.make_runner.run": "per-worker ledgers + shared cross-agent monitor",
    "chimera/cli/main.py:crew_isolated.make_factory.factory": "per-worker ledgers, shared taint view",
    "chimera/cli/main.py:desktop_app.factory": (
        "applies _apply_tool_allowlist and the optional chat guard itself. A SECOND implementation "
        "of what this profile does, kept because it also carries MCP connectors the profile does "
        "not model — consolidating the two is open work, not a settled answer"
    ),
    "chimera/api/code_api.py:assemble_registry": (
        "resolves its own posture floor + taint narrowing, and installs the deployment's "
        "kernel itself via govern_step — the profile would have built a SECOND TaintLedger "
        "over the one this surface hands back to its caller. Pinned by "
        "tests/test_governance_on_the_api_path.py, because an exemption is prose and prose "
        "goes stale"
    ),
    # `AgentLane.run` was exempted here on the grounds that `restrict_registry` from the card's own
    # role was fail-closed. It was not: `AgentDef.allowed_tools` defaults to `[]`, `as_role` turns
    # that into `None`, and `restrict_registry` reads `None` as "keep every tool". So the exemption
    # covered the ordinary case — a subagent registered without ticking any tool boxes — and that
    # subagent ran the full autonomous loop outside the owner's denylist, trust kernel, taint ledger
    # and audit trail, on the same board where `SolveLane` beside it was governed.
    #
    # It now takes `governed_profile` first and narrows to the role second, so the exemption is
    # gone rather than reworded. This is the SECOND time this file's exemption for that qualname
    # hid an ungoverned surface: the note in `lanes.py` records the first, where three classes
    # sharing a method named `run` let the exemption cover `SolveLane` as well.
    # --- not an agent surface at all ---
    "chimera/api/app.py:build_api_app.tools_endpoint": "lists tool schemas; nothing is invoked",
    "chimera/cli/main.py:tools": "prints the tool table",
    "chimera/cli/main.py:schema_bench": "benchmark harness, no deployment",
    "chimera/cli/main.py:sandbox_bench.factory": "benchmark harness, no deployment",
    "chimera/cli/main.py:evolve_tune": "offline tuning over recorded trajectories",
    "chimera/cli/main.py:meta": "designs an agent blueprint; does not run one",
    "chimera/core/agent.py:_default_skill_registry": "internal default, wrapped by whoever built it",
    "chimera/orchestration/lifecycle.py:lifecycle_crew": "library constructor; the caller governs",
    "chimera/workflow/executors.py:build_executors.solve_step": "delegates to solve, governed there",
}


def _qualname(chain: list[str]) -> str:
    return ".".join(chain) or "(module)"


def _bare_registry_calls(tree: ast.Module, module: str) -> list[tuple[str, int]]:
    """``(key, lineno)`` for every `default_registry(...)` this module builds outside the profile.

    An AST walk rather than a grep, because a grep cannot tell an assembled registry from the same
    words inside a docstring — and this test exists precisely to survive the next person who writes
    a comment about it. Nested functions are walked with their enclosing chain, because that is where
    it went missing both times: `serve` builds its registry inside a `factory()` closure, and so did
    the messaging manager.

    A call is legitimate when it is an argument to `governed_profile`.
    """
    governed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "governed_profile":
            for arg in node.args:
                for inner in ast.walk(arg):
                    if isinstance(inner, ast.Call):
                        governed.add(id(inner))

    found: list[tuple[str, int]] = []

    def walk(node: ast.AST, chain: list[str]) -> None:
        # ClassDef belongs in the chain, and leaving it out was not cosmetic. `lanes.py` holds
        # three classes with a method called `run`; without the class name all three collapsed
        # onto the key `chimera/kanban/lanes.py:run`, so the single exemption written for
        # `AgentLane.run` — which really does restrict the registry — silently covered
        # `SolveLane.run`, which builds a bare one. A gate whose key cannot tell two call sites
        # apart exempts the wrong one and stays green about it.
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            chain = [*chain, node.name]
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "default_registry"
            and id(node) not in governed
        ):
            found.append((f"{module}:{_qualname(chain)}", node.lineno))
        for child in ast.iter_child_nodes(node):
            walk(child, chain)

    walk(tree, [])
    return found


def _ungoverned_sites() -> list[tuple[str, int]]:
    """Every site in the package that builds a registry outside the profile and is not exempt."""
    out: list[tuple[str, int]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "default_registry(" not in source:
            continue
        module = path.relative_to(PACKAGE.parent).as_posix()
        out.extend(
            (key, line)
            for key, line in _bare_registry_calls(ast.parse(source), module)
            if key not in EXEMPT
        )
    return out


def test_no_surface_builds_a_registry_outside_the_profile_unless_it_says_why() -> None:
    """The test that makes the guarantee structural — over the whole package, not one file.

    Surfaces lose their governance through absence rather than decision: each is written at a
    different time and none calls the thing the others call. A convention cannot fix that. A build
    that fails can — but only if it looks everywhere, which the first version of this did not.
    """
    ungoverned = _ungoverned_sites()

    assert not ungoverned, (
        "a surface assembles tools without governed_profile and is not in EXEMPT: "
        + ", ".join(f"{key} (line {line})" for key, line in ungoverned)
        + " — either route it through governed_profile, or add it to EXEMPT with the reason it "
        "does not need to be. An unlisted surface means the deployment's allowlist and taint "
        "ledger are true only where someone remembered."
    )


def test_the_messaging_manager_is_covered_by_the_walk() -> None:
    """The specific regression, pinned by name.

    The blind spot was not that `manager.py` was wrong — it was that the gate could not see it. A
    walk that silently stopped covering this file again would leave the same bot unfenced with a
    green build, so the coverage itself is asserted rather than assumed.
    """
    source = (PACKAGE / "server" / "manager.py").read_text(encoding="utf-8")

    assert "default_registry(" in source, "the file no longer builds a registry — retarget this test"
    assert "governed_profile(" in source, "the app's messaging bot lost its governance again"
    assert not [
        key for key, _ in _bare_registry_calls(ast.parse(source), "chimera/server/manager.py")
    ], "manager.py builds a registry outside the profile"


def test_every_exemption_still_points_at_real_code() -> None:
    """An exemption list that outlives the code it exempts is how a gate rots into a formality.

    A stale key is not harmless: it is a line saying "this was considered" about a site that no
    longer exists, next to sites that do.
    """
    live = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "default_registry(" not in source:
            continue
        module = path.relative_to(PACKAGE.parent).as_posix()
        live.update(key for key, _ in _bare_registry_calls(ast.parse(source), module))

    stale = sorted(set(EXEMPT) - live)

    assert not stale, f"EXEMPT names sites that no longer build a bare registry: {stale}"


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

    assert _bare_registry_calls(ast.parse(source), "m.py") == [("m.py:serve.factory", 4)]


def test_a_governed_call_is_not_flagged() -> None:
    # The other half: the walk must recognise the legitimate shape, or every governed surface would
    # have to be exempted and the list would stop meaning anything.
    source = textwrap.dedent(
        """
        def serve():
            registry, _ = governed_profile(default_registry(ws), settings=s, home=h)
        """
    )

    assert _bare_registry_calls(ast.parse(source), "m.py") == []


def test_the_gate_does_not_demand_a_refactor_of_the_attended_commands() -> None:
    # Otherwise it would rewrite `solve` under the banner of a security fix — and a gate that fires
    # on things it was not built to defend gets weakened until it fires on nothing. The exemption is
    # what expresses that, so what is asserted is that the exemption is doing the work.
    assert "chimera/cli/main.py:solve._run_solve" in EXEMPT
    assert not [key for key, _ in _ungoverned_sites() if key.endswith(":chat")]


@pytest.mark.parametrize(
    ("surface", "module"),
    [
        ("serve", "cli/main.py"),
        ("cron", "cli/main.py"),
        ("mcp", "cli/main.py"),
        ("a2a", "cli/main.py"),
        ("platform", "cli/main.py"),
        # The app's Messaging toggle gets its OWN label rather than reusing "platform": an audit
        # reading these counts has to be able to say which of the two ways the bot was started,
        # because for three weeks only one of them was governed at all.
        ("app-messaging", "server/manager.py"),
        # The two HTTP surfaces that reach `assemble_registry`. Added with the kernel that now runs
        # there: the labels were written and not declared, which is the exact shape this gate exists
        # to catch — cheap to drop while refactoring, and invisible once dropped.
        ("api:run", "api/app.py"),
        ("api:turn", "api/code_api.py"),
        # The hierarchy's workers. Read-only and not request-configurable, but still assembled
        # through the governed path — an audit line has to be able to name this entry point too.
        ("api:hierarchy", "api/orchestration_api.py"),
        # The crew's workers WRITE, each in its own worktree, under one shared taint ledger. Its
        # own label rather than reusing the hierarchy's: an audit reading these counts has to be
        # able to tell a read-only fan-out from a team editing files.
        ("api:crew", "api/orchestration_api.py"),
    ],
)
def test_each_named_surface_is_declared(surface: str, module: str) -> None:
    # The `surface=` label is what makes an audit line answerable ("which entry point was that?").
    # Cheap to drop while refactoring, and invisible once dropped.
    body = (PACKAGE / module).read_text(encoding="utf-8")

    assert f'surface="{surface}' in body or f'surface=f"{surface}' in body


# --- the fence, and what it is NOT filed under ----------------------------------------------------


def test_an_explicit_denylist_applies_with_governance_off(tmp_path: pathlib.Path) -> None:
    """The gap this closes.

    `CHIMERA_GOVERNANCE` defaults to `off`, and `off` used to return the registry untouched — so an
    owner who wrote `CHIMERA_TOOL_DENYLIST=write_file` in `.env` got no fence on any unattended
    surface, while `config.py` said the lists apply everywhere. The two are not the same kind of
    thing: a denylist is an instruction, and there is nothing to stage about removing a named tool.
    """
    registry, _ = governed_profile(
        _registry(),
        settings=_settings(tmp_path, CHIMERA_TOOL_DENYLIST="write_file"),
        home=tmp_path,
    )

    assert "write_file" not in {t.name for t in registry.tools()}, (
        "the owner's fence did not reach an off-mode surface"
    )


def test_an_explicit_allowlist_applies_with_governance_off(tmp_path: pathlib.Path) -> None:
    """The other half: a non-empty allowlist grants ONLY those, everything else is dropped."""
    registry, _ = governed_profile(
        _registry(),
        settings=_settings(tmp_path, CHIMERA_TOOL_ALLOWLIST="read_file"),
        home=tmp_path,
    )

    # `_Writer` is the only tool in the fixture and it is not `read_file`, so an allowlist that
    # names something else must leave nothing behind — fail-closed, not fail-open.
    assert [t.name for t in registry.tools()] == []


def test_setting_no_list_still_returns_the_very_same_registry(tmp_path: pathlib.Path) -> None:
    """The blast radius, asserted rather than claimed.

    Both lists default to empty, both resolve to "no restriction", and the restrict call is skipped
    — so a deployment that set neither is handed back the identical object. That identity check is
    what makes this a fix for people who wrote a fence and not a change for everybody else.
    """
    raw = _registry()

    wrapped, approvals = governed_profile(raw, settings=_settings(tmp_path), home=tmp_path)

    assert wrapped is raw
    assert not approvals.granted and not approvals.refused


def test_the_inferential_half_is_still_staged(tmp_path: pathlib.Path) -> None:
    """What must NOT have moved. The kernel and the taint ledger can refuse legitimate work, which
    is the whole reason `observe` exists — turning those on by default under cover of a fence fix
    would be exactly the breaking change this avoids."""
    registry, _ = governed_profile(
        _registry(),
        settings=_settings(tmp_path, CHIMERA_TOOL_DENYLIST="nothing_by_this_name"),
        home=tmp_path,
    )
    tool = registry.get("write_file")

    assert tool is not None
    assert type(tool).__name__ == "_Writer", "off mode wrapped the tool in governance machinery"


def test_the_fence_reaches_the_in_app_bot_with_governance_off(tmp_path: pathlib.Path) -> None:
    """End to end on the surface that started this: the app's Messaging toggle, stock config."""
    from chimera.config import Settings
    from chimera.providers import CompletionResult
    from chimera.server import MessagingManager

    class _Backend:
        def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
            return CompletionResult(content="ok", model="fake")

    class _Adapter:
        platform = "discord"

        def send(self, *a: Any, **k: Any) -> str:
            return "ok"

    settings = Settings(
        CHIMERA_HOME=str(tmp_path),
        CHIMERA_DISCORD_BOT_TOKEN="t",
        CHIMERA_TOOL_DENYLIST="run_shell",
        # governance deliberately left at its default `off`
    )
    adapter = _Adapter()
    manager = MessagingManager(
        settings=settings, backend=_Backend(), model=None, max_steps=4,
        workspace=tmp_path, adapter_factory=lambda _p: adapter,
    )
    on_message = manager._gateway_on_message(adapter)
    names = {t.name for t in on_message.__self__.session_for("c").agent.tools.tools()}  # type: ignore[attr-defined]

    assert "run_shell" not in names
    assert "send_message" in names
