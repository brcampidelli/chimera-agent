"""``send_message`` sits inside the bot's governance, not beside it.

Every bot surface — ``chimera serve --discord`` (``_serve_platform``), the HTTP ``serve`` with a
push sender, and the app's Messaging toggle (``server/manager.py``) — registered ``send_message``
AFTER ``governed_profile`` returned. The stated reason was right as far as it went: a denylist aimed
at the shell must not take the bot's own voice away. But registering after the profile also
registered after the trust kernel and the taint ledger, so the one tool that sends text to an
ARBITRARY chat was the one tool neither of them saw: once a turn had read an attacker's page, the
narrowing that holds every other dangerous tool (``send_message`` is in
``DANGEROUS_WHEN_TAINTED``) did not hold this one, and nothing recorded where the text went.

Now the tool is handed to ``governed_profile`` as a ``voice`` tool: registered after the explicit
fence — still exempt from it, the reason above stands — and before the kernel and the ledger.

The structural test is the build gate for the next surface; the behavioural ones drive the three
real factories under ``CHIMERA_GOVERNANCE=enforce`` and hand ``send_message`` a tainted run.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.config import Settings, get_settings

PAGE = "https://attacker.test/page"
LONG = "conteudo externo " * 8  # over the verbatim-flow floor, so the run is genuinely tainted
_ROOT = Path(__file__).resolve().parents[1] / "chimera"


class _Adapter:
    platform = "discord"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, chat_id: str, text: str) -> str:
        self.sent.append((chat_id, text))
        return "sent"

    def start(self, _route: Any) -> None:  # pragma: no cover - never started here
        raise AssertionError("the adapter must never be started")

    def stop(self) -> None:
        return None


def _ledgered_send(registry: Any) -> Any:
    tool = registry.get("send_message")
    assert tool is not None, "the bot has no send_message at all"
    return tool


def _taint(registry: Any) -> None:
    """Taint the SESSION's ledger — the one its other tools are wrapped in — as a fetch would."""
    ledgers = {id(t.ledger): t.ledger for t in registry.tools() if getattr(t, "ledger", None)}
    assert len(ledgers) == 1, "precondition: one governed session, one ledger"
    (ledger,) = ledgers.values()
    ledger.record_fetch(PAGE, content=LONG)
    assert ledger.run_tainted()


def _exfiltrate(tool: Any) -> str:
    return str(tool.run(platform="discord", chat_id="attacker-chat", text="the owner's notes"))


# -- the three factories ---------------------------------------------------------------------------


def _cli_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], adapter: _Adapter) -> Any:
    """The session ``serve`` / ``serve --discord`` builds, intercepted at ``MessageGateway``."""
    import chimera.cli.main as cli
    import chimera.server as server_pkg
    from chimera.integrations import SenderRegistry

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("CHIMERA_OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("CHIMERA_GOVERNANCE", "enforce")
    get_settings.cache_clear()
    captured: dict[str, Any] = {}

    def fake_gateway(factory: Any, *args: Any, **kwargs: Any) -> Any:
        captured["session"] = factory()
        raise SystemExit(0)

    def senders(_settings: Any, extra: Any = None) -> Any:
        registry = SenderRegistry()
        registry.register(adapter)
        return registry

    monkeypatch.setattr(server_pkg, "MessageGateway", fake_gateway)
    monkeypatch.setattr(cli, "_messaging_adapter", lambda _s, _p, **_k: adapter)
    monkeypatch.setattr(cli, "_sender_registry", senders)
    CliRunner().invoke(cli.app, [*argv, "--workspace", str(tmp_path), "--no-memory"])
    get_settings.cache_clear()
    assert "session" in captured, "the command never built a chat session"
    return captured["session"].agent.tools


def _manager_registry(tmp_path: Path, adapter: _Adapter) -> Any:
    from chimera.providers import CompletionResult
    from chimera.server import MessagingManager

    class Backend:
        def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
            return CompletionResult(content="ok", model="fake")

    settings = Settings(
        CHIMERA_HOME=str(tmp_path), CHIMERA_DISCORD_BOT_TOKEN="t", CHIMERA_GOVERNANCE="enforce"
    )
    mgr = MessagingManager(
        settings=settings, backend=Backend(), model=None, max_steps=4,
        workspace=tmp_path, adapter_factory=lambda _p: adapter,
    )
    gateway = mgr._gateway_on_message(adapter).__self__  # type: ignore[attr-defined]
    return gateway.session_for("some-chat").agent.tools


SURFACES = ["platform", "http", "app"]


def _registry(surface: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter: _Adapter) -> Any:
    if surface == "platform":
        return _cli_session(tmp_path, monkeypatch, ["serve", "--discord"], adapter)
    if surface == "http":
        return _cli_session(tmp_path, monkeypatch, ["serve"], adapter)
    return _manager_registry(tmp_path, adapter)


@pytest.mark.parametrize("surface", SURFACES)
def test_send_message_is_inside_the_taint_ledger(
    surface: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _ledgered_send(_registry(surface, tmp_path, monkeypatch, _Adapter()))
    assert getattr(tool, "ledger", None) is not None, f"{surface}: send_message skips the ledger"


@pytest.mark.parametrize("surface", SURFACES)
def test_a_tainted_turn_cannot_message_an_arbitrary_chat(
    surface: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exfiltration row: a turn that read an attacker's page tells the bot to send the owner's
    notes to the attacker's chat id. On a bot nobody can approve a card, so it is refused."""
    adapter = _Adapter()
    registry = _registry(surface, tmp_path, monkeypatch, adapter)
    _taint(registry)

    _exfiltrate(_ledgered_send(registry))

    assert adapter.sent == [], f"{surface}: the tainted send went out"


@pytest.mark.parametrize("surface", SURFACES)
def test_a_clean_turn_still_speaks(
    surface: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction: governance on must not cost the bot its voice."""
    adapter = _Adapter()
    tool = _ledgered_send(_registry(surface, tmp_path, monkeypatch, adapter))

    _exfiltrate(tool)

    assert adapter.sent == [("attacker-chat", "the owner's notes")]


def test_no_surface_registers_a_send_tool_after_its_profile() -> None:
    """The build gate: ``registry.register(<...send...>)`` after ``governed_profile`` is the shape
    that kept this tool ungoverned on three surfaces at once."""
    offenders = []
    for path in (_ROOT / "cli" / "main.py", _ROOT / "server" / "manager.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "register"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and "send" in node.args[0].id
            ):
                offenders.append(f"{path.name}:{node.lineno} registers {node.args[0].id}")
    assert not offenders, "\n".join(offenders)


def test_under_off_the_voice_is_kept_but_nothing_governs_it(tmp_path: Path) -> None:
    """The scope of this fix, pinned so a docstring cannot overclaim it again: under ``off`` the
    voice tool is registered and still exempt from the fence, and no ledger or kernel wraps it —
    because under ``off`` none wraps anything. A tainted send there is neither narrowed nor recorded;
    making it recorded is a separate decision about what ``off`` means."""
    from chimera.governance.profile import governed_profile
    from chimera.integrations import SenderRegistry
    from chimera.integrations.messaging import SendMessageTool
    from chimera.tools import ToolRegistry

    senders = SenderRegistry()
    senders.register(_Adapter())
    send = SendMessageTool(senders)
    settings = Settings(_env_file=None, CHIMERA_HOME=str(tmp_path), CHIMERA_GOVERNANCE="off")  # type: ignore[call-arg]  # aliases are runtime-only init kwargs

    registry, _ = governed_profile(
        ToolRegistry(), settings=settings, home=tmp_path, deny="send_message", voice=[send]
    )

    tool = registry.get("send_message")
    assert tool is send, "under off the voice must arrive unwrapped — and must arrive"
