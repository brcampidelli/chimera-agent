"""``chimera serve`` and the platform bots tell their taint ledger the user's message.

Both build one registry per conversation inside a ``factory()`` closure, by calling
``governed_profile(...)`` **without** ``instruction=`` — which that function's own docstring says is
right for "a surface with no single task". It is right about the RUN and wrong about the surface:
a gateway session sees every turn, and a ledger nobody tells an instruction answers ``unknown`` for
every fetch, which the narrowing treats exactly as it treats ``agent``. So
``CHIMERA_TAINT_AUTHORITY`` had nothing to be a mode about here — the same defect ``chimera chat``
fixed in #400 with ``RightHand.begin_turn`` and the desktop app fixed in #408 with
``ChatSession.on_turn_start``.

Measured before the fix on the terminal's own instrument, one run
(``bench/right_hand_governance/RESULTS.md`` §8b): ``CHIMERA_TAINT_AUTHORITY=authority`` moves
**0 rows** on the ledger nobody tells and **9** on the ledger told the turn's message.

**Why this file drives the real commands.** #400 shipped four checks that all asked whether a
command *called* its builder, and a sabotage that called the builder and then overwrote the result
with a bare registry passed **0 of 109** of them. So the question here is never "was a hook wired"
but "what did the ledger the session's own tools are wrapped in answer when a turn arrived".

**And why every one of these sets ``CHIMERA_GOVERNANCE``.** This surface is the one that does not
build a ledger unconditionally: ``governed_profile``'s ``if step.mode == "off": return`` sits above
its ``TaintLedger`` line, and ``off`` is the shipped default. A test written without the variable
would pass on a surface with no ledger at all, for the same reason the bench needed a governance
axis — and the stock-default case is asserted on purpose below, as its own claim.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.interface.session import ChatSession

PAGE = "https://example.test/page"


# --------------------------------------------------------------------------- the seam itself


def _stub_registry() -> Any:
    from chimera.tools.base import Tool

    class _T(Tool):
        def __init__(self) -> None:
            self.name = "http_get"
            self.description = "stand-in"
            self.parameters: dict[str, Any] = {"type": "object", "properties": {}}
            self.untrusted_output = True

        def run(self, **kwargs: Any) -> str:
            return "payload"

    from chimera.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(_T())
    return registry


def _settings(tmp_path: Path, **extra: str) -> Any:
    from chimera.config import Settings

    return Settings(CHIMERA_HOME=str(tmp_path), **extra)  # type: ignore[arg-type]


def test_governed_profile_hands_back_the_ledger_it_built(tmp_path: Path) -> None:
    """The seam, asked of the object the tools were wrapped around rather than of the call."""
    from chimera.governance.profile import governed_profile

    seen: list[Any] = []
    registry, _ = governed_profile(
        _stub_registry(),
        settings=_settings(tmp_path, CHIMERA_GOVERNANCE="enforce"),
        home=tmp_path,
        surface="test",
        on_ledger=seen.append,
    )
    assert len(seen) == 1, "governed_profile did not hand its ledger to the caller that asked"
    assert seen[0] is registry.get("http_get").ledger, (
        "the caller was handed a DIFFERENT ledger from the one the tools are wrapped around — "
        "an instruction set on it would narrow nothing"
    )


def test_a_caller_that_does_not_ask_is_unaffected(tmp_path: Path) -> None:
    """The property that made the same fix safe for the messaging gateway in #408, kept here.

    Ten call sites unpack this function's 2-tuple and none of them passes ``on_ledger``; the default
    has to leave every one of them exactly as it was.
    """
    from chimera.governance.profile import governed_profile

    settings = _settings(tmp_path, CHIMERA_GOVERNANCE="enforce")
    plain, plain_approvals = governed_profile(
        _stub_registry(), settings=settings, home=tmp_path, surface="test"
    )
    asked, asked_approvals = governed_profile(
        _stub_registry(), settings=settings, home=tmp_path, surface="test", on_ledger=lambda _l: None
    )
    assert [type(t).__name__ for t in plain.tools()] == [
        type(t).__name__ for t in asked.tools()
    ]
    assert type(plain_approvals) is type(asked_approvals)
    assert plain.get("http_get").ledger.instruction is None
    assert asked.get("http_get").ledger.instruction is None


def test_no_callback_when_governance_is_off(tmp_path: Path) -> None:
    """There is no ledger to hand over under the shipped default, and the caller must be able to
    tell — ``governed_profile`` returns above its ``TaintLedger`` line when the mode is ``off``.

    This is the fact that separates this surface from the other three: ``build_right_hand`` and
    ``guard_chat_registry`` both build a ledger whatever the mode.
    """
    from chimera.governance.profile import governed_profile

    settings = _settings(tmp_path)
    assert settings.governance_mode == "off", "the shipped default moved; retarget this test"
    seen: list[Any] = []
    registry, _ = governed_profile(
        _stub_registry(), settings=settings, home=tmp_path, surface="test", on_ledger=seen.append
    )
    assert seen == [], "a ledger was handed over on a surface that builds none"
    assert getattr(registry.get("http_get"), "ledger", None) is None


def test_governed_profile_reads_the_settings_it_is_given(tmp_path: Path) -> None:
    """Pins the reason the bench's gateway arm needs no ``_as_process_settings``.

    The ``app_chat`` arm needs that contextmanager because ``guard_chat_registry`` reads the
    process-wide ``get_settings()`` and ignores any ``Settings`` a caller is holding — and the first
    version of that arm reported a finding it could not have measured because of it. This function
    takes ``settings=`` explicitly. Asserted rather than assumed, so the day it starts reaching for
    the process-wide object the bench finds out here instead of by printing a plausible zero.
    """
    import chimera.config as config
    from chimera.governance.profile import governed_profile

    def _explode() -> Any:  # pragma: no cover - the point is that it is never called
        raise AssertionError("governed_profile read the process-wide settings")

    original = config.get_settings
    config.get_settings = _explode  # type: ignore[assignment]
    try:
        registry, _ = governed_profile(
            _stub_registry(),
            settings=_settings(tmp_path, CHIMERA_GOVERNANCE="enforce", CHIMERA_TAINT_AUTHORITY="authority"),
            home=tmp_path,
            surface="test",
        )
    finally:
        config.get_settings = original  # type: ignore[assignment]
    assert registry.get("http_get").ledger.authority == "authority"


# --------------------------------------------------------------------------- the wiring


def _captured_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], *, governance: str | None
) -> ChatSession:
    """The session one of the two gateway factories builds, from the command itself.

    Both factories are closures inside a command body, so each is reached the way the gateway
    reaches it: by running the real command far enough to build one and intercepting
    ``MessageGateway``, which is the first thing that receives it.
    """
    import chimera.cli.main as cli

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("CHIMERA_OPENROUTER_API_KEY", "test-key")  # `serve` exits without one
    monkeypatch.delenv("CHIMERA_GOVERNANCE", raising=False)
    if governance is not None:
        monkeypatch.setenv("CHIMERA_GOVERNANCE", governance)
    from chimera.config import get_settings

    get_settings.cache_clear()

    captured: dict[str, Any] = {}

    def fake_gateway(factory: Any, *args: Any, **kwargs: Any) -> Any:
        # TWO calls, because `MessageGateway` builds one session per `chat_id` and the isolation
        # between them is a property this fix could break. `_captured_session` returns the first.
        captured["session"] = factory()
        captured["second"] = factory()
        raise SystemExit(0)  # nothing past this point is this test's business

    # Patched where it is DEFINED: both command bodies do `from chimera.server import
    # MessageGateway` inside themselves, so an attribute on the cli module is not what resolves.
    import chimera.server as server_pkg

    monkeypatch.setattr(server_pkg, "MessageGateway", fake_gateway)
    monkeypatch.setattr(cli, "_messaging_adapter", lambda _s, _p: _FakeAdapter())
    CliRunner().invoke(cli.app, [*argv, "--workspace", str(tmp_path), "--no-memory"])
    get_settings.cache_clear()
    session = captured.get("session")
    assert isinstance(session, ChatSession), "the command never built a chat session"
    _SECOND[:] = [captured["second"]]
    return session


#: The second session the last `_captured_session` built, for the isolation test below. A module
#: global rather than a changed return type, so the twelve tests above keep reading as they did.
_SECOND: list[Any] = []


class _FakeAdapter:
    """A platform adapter that never connects to anything. `--discord` is the route, not the point."""

    platform = "discord"

    def send(self, chat_id: str, text: str) -> str:  # pragma: no cover - never reached
        return "sent"

    def start(self, on_message: Any) -> None:  # pragma: no cover - MessageGateway raises first
        raise AssertionError("the fake adapter must never be started")

    def stop(self) -> None:
        return None


def _ledger_behind(session: ChatSession) -> Any:
    """The taint ledger the session's own tools are wrapped in.

    Reached through the registry rather than through a field the test asked to be given, so a wire
    that points at some *other* ledger fails here instead of passing.
    """
    registry = session.agent.tools  # type: ignore[attr-defined]  # `Agent.tools` IS the registry
    for tool in registry.tools():
        ledger = getattr(tool, "ledger", None)
        if ledger is not None:
            return ledger
    raise AssertionError("no ledgered tool in the gateway's registry — governance did not apply")


#: ``serve`` is the HTTP gateway; ``serve --discord`` routes to ``_serve_platform``. Both are driven
#: rather than one: this is the same defect in two closures, and the last time one of these was
#: fixed the other was not asked about.
SURFACES = [
    pytest.param(["serve"], id="serve"),
    pytest.param(["serve", "--discord"], id="platform"),
]


def test_the_two_surfaces_are_actually_two_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both parametrised ids reach a DIFFERENT closure — checked, not assumed.

    ``CliRunner`` swallows exceptions, so a ``--discord`` that quietly failed to route would leave
    every ``platform`` case above silently measuring ``serve``'s factory a second time: four green
    tests about a surface nobody touched. The tell is ``send_message``, which only
    ``_serve_platform`` registers (``serve`` builds one solely when a push sender is configured, and
    none is here).
    """
    http = _captured_session(tmp_path, monkeypatch, ["serve"], governance="enforce")
    platform = _captured_session(tmp_path, monkeypatch, ["serve", "--discord"], governance="enforce")

    def _names(session: ChatSession) -> set[str]:
        return {t.name for t in session.agent.tools.tools()}  # type: ignore[attr-defined]

    assert "send_message" in _names(platform), "--discord did not reach _serve_platform"
    assert "send_message" not in _names(http), "the two arms are the same closure"


@pytest.mark.parametrize("argv", SURFACES)
def test_the_gateway_hands_its_session_a_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """The wiring, asked of the object the command built rather than of the source that built it."""
    session = _captured_session(tmp_path, monkeypatch, argv, governance="enforce")
    assert session.on_turn_start is not None, (
        "the gateway's chat session carries no turn hook, so its ledger is never told the "
        "instruction and CHIMERA_TAINT_AUTHORITY is inert again"
    )


@pytest.mark.parametrize("argv", SURFACES)
def test_the_hook_reaches_the_ledger_the_registry_is_using(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """The half a hook-exists check cannot see: that it points at THIS session's ledger.

    Asked through the ledger's own answer rather than through a captured object — ``requester_of``
    goes from ``unknown`` (nobody told it) to ``user`` (the person named that page), which is
    exactly the transition ``authority`` mode reads.
    """
    session = _captured_session(tmp_path, monkeypatch, argv, governance="enforce")
    ledger = _ledger_behind(session)

    assert ledger.requester_of(PAGE) == "unknown"
    assert session.on_turn_start is not None
    session.on_turn_start(f"please summarise {PAGE}")
    assert ledger.requester_of(PAGE) == "user"
    assert ledger.requester_of("https://somewhere.else/page") == "agent"


@pytest.mark.parametrize("argv", SURFACES)
def test_a_turn_through_send_tells_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """Through ``ChatSession.send``, not through the hook by hand.

    A hook that exists and is never called is the shape #400's sabotage had. The gateway serves its
    turns through ``send`` (``MessageGateway.on_message``), so that is the entry point asked here.
    """
    session = _captured_session(tmp_path, monkeypatch, argv, governance="enforce")
    ledger = _ledger_behind(session)

    class _Agent:
        tools = session.agent.tools  # type: ignore[attr-defined]

        def run(self, prompt: str, **kwargs: Any) -> Any:
            from chimera.core.agent import AgentResult

            # Asked DURING the turn: an instruction set after the tools ran is an instruction the
            # narrowing never saw.
            assert ledger.requester_of(PAGE) == "user"
            return AgentResult(answer="ok", steps=1, stopped_reason="final", tool_names=[])

    session.agent = _Agent()  # type: ignore[assignment]
    assert ledger.requester_of(PAGE) == "unknown"
    assert session.send(f"please summarise {PAGE}") == "ok"
    assert ledger.requester_of(PAGE) == "user"


@pytest.mark.parametrize("argv", SURFACES)
def test_two_chats_do_not_share_a_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """One session per `chat_id`, one ledger per session — and one turn hook per ledger.

    This is the property a naive version of the fix loses. Hoisting `turn_ledger` out of `factory()`
    to save four lines would compile, pass every test above, and give every chat on a Discord server
    the last-built ledger: person A's message would decide what counts as person B's own request,
    which is the `authority` mode pointed at the wrong person. `nonlocal` inside the closure is what
    keeps each call's cell its own, and nothing else in the file would notice if it stopped.
    """
    first = _captured_session(tmp_path, monkeypatch, argv, governance="enforce")
    second = _SECOND[0]

    first_ledger, second_ledger = _ledger_behind(first), _ledger_behind(second)
    assert first_ledger is not second_ledger, "two chats were handed one taint ledger"

    assert first.on_turn_start is not None
    first.on_turn_start(f"read {PAGE}")
    assert first_ledger.requester_of(PAGE) == "user"
    assert second_ledger.requester_of(PAGE) == "unknown", (
        "one chat's message reached another chat's ledger — under authority that decides what "
        "counts as somebody else's own request"
    )


@pytest.mark.parametrize("argv", SURFACES)
def test_the_stock_deployment_is_left_exactly_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """With ``CHIMERA_GOVERNANCE`` unset there is no ledger, so there must be no hook either.

    Not a nicety. A hook wired to a ledger that does not exist would be an attribute error on the
    first message of a stock deployment — and a hook wired to *something* would be a claim that this
    surface has a governance layer it does not have. The honest state is ``None``, which is also
    byte-identical to what ``ChatSession`` got before this change.
    """
    session = _captured_session(tmp_path, monkeypatch, argv, governance=None)
    assert session.on_turn_start is None
    registry = session.agent.tools  # type: ignore[attr-defined]
    assert all(getattr(t, "ledger", None) is None for t in registry.tools())
    assert session.send is not None  # the session is usable; nothing was half-built


# --------------------------------------------------------------------------- the instrument

_BENCH = (
    Path(__file__).resolve().parents[1]
    / "bench"
    / "right_hand_governance"
    / "run_terminal_vs_governed.py"
)


@pytest.fixture(scope="module")
def bench() -> Any:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("run_terminal_vs_governed", _BENCH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE execution: the bench declares dataclasses, and `@dataclass` resolves its
    # annotations through `sys.modules[cls.__module__]`.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fingerprint(outcomes: list[Any]) -> str:
    return "\n".join(f"{o.id}|{o.ran}|{o.mechanism}|{o.read_fenced}" for o in outcomes)


def test_the_gateway_arm_moves_when_it_is_told_and_not_when_it_is_not(
    bench: Any, tmp_path: Path
) -> None:
    """The power half of §8b's zero, without which that column is a blind instrument.

    ``serve_untold`` reading ``identical, 0 rows moved`` under ``CHIMERA_TAINT_AUTHORITY=authority``
    is the finding. It is only a finding if the same probe, on the same corpus, registers the change
    on the arm that IS told — otherwise the zero is what an instrument prints when it cannot see.
    A ``gateway_registry`` that silently dropped its ``instruction`` argument would make all four
    columns read zero and the table would say the lever is dead, which is exactly the shape this
    project keeps paying for.
    """
    episodes = bench.attack_episodes() + bench.benign_episodes()
    named = f"Summarise {bench.ATTACK_PAGE} and {bench.UPSTREAM_PAGE} for me"
    on = _settings(tmp_path, CHIMERA_GOVERNANCE="enforce")
    authority = _settings(
        tmp_path, CHIMERA_GOVERNANCE="enforce", CHIMERA_TAINT_AUTHORITY="authority"
    )

    for told, untold in (("serve", "serve_untold"), ("platform", "platform_untold")):
        base = _fingerprint(bench.run_arm(episodes, on, tmp_path, arm=told, instruction=named))
        moved = _fingerprint(
            bench.run_arm(episodes, authority, tmp_path, arm=told, instruction=named)
        )
        untold_base = _fingerprint(
            bench.run_arm(episodes, on, tmp_path, arm=untold, instruction=named)
        )
        untold_moved = _fingerprint(
            bench.run_arm(episodes, authority, tmp_path, arm=untold, instruction=named)
        )
        assert moved != base, f"CHIMERA_TAINT_AUTHORITY is inert on the {told} arm that WAS told"
        assert untold_moved == untold_base, (
            f"the {untold} arm moved under authority — it is supposed to model the surface with no "
            "instruction, so either it is being told one or the fingerprint is unstable"
        )


def test_the_gateway_arms_agree_with_each_other(bench: Any, tmp_path: Path) -> None:
    """One function builds both, so a run where they diverge is a run where the seam is lying.

    The two arms differ in the ``surface=`` label and in nothing that decides what is allowed —
    which is exactly what the two shipped call sites differ in. Keeping both is how a future
    divergence gets to show up as a failure instead of as a table nobody reads twice.
    """
    episodes = bench.attack_episodes() + bench.benign_episodes()
    named = f"Summarise {bench.ATTACK_PAGE} and {bench.UPSTREAM_PAGE} for me"
    settings = _settings(tmp_path, CHIMERA_GOVERNANCE="enforce")
    serve = _fingerprint(bench.run_arm(episodes, settings, tmp_path, arm="serve", instruction=named))
    platform = _fingerprint(
        bench.run_arm(episodes, settings, tmp_path, arm="platform", instruction=named)
    )
    assert serve == platform


def test_the_gateway_arm_has_no_ledger_under_the_shipped_default(bench: Any, tmp_path: Path) -> None:
    """The reason §8b has a governance axis at all, asserted rather than described in prose.

    Every other arm in that file builds a ledger unconditionally. This one does not, and a table
    measured only at the default would print four zeros before AND after the fix.
    """
    registry = bench.gateway_registry(
        bench.build_stub_registry(_settings(tmp_path), {}),
        _settings(tmp_path),
        tmp_path,
        surface="serve",
    )
    assert bench._ledger_in(registry) is None

    governed = bench.gateway_registry(
        bench.build_stub_registry(_settings(tmp_path), {}),
        _settings(tmp_path, CHIMERA_GOVERNANCE="enforce"),
        tmp_path,
        surface="serve",
    )
    assert bench._ledger_in(governed) is not None
