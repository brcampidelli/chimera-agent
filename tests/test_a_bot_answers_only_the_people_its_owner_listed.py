"""A chat bot answers only the ids its owner listed — and says so loudly when the list is empty.

Every adapter has accepted ``allowed_users`` since it shipped (``None`` = anyone), and no construction
path ever passed it: `chimera serve --discord` and the app's Messaging toggle both built the bot from
its token alone, so the bot answered whoever could reach it, with the owner's tools and spend. The
adapters' own tests were green the whole time, because they built the adapter with the keyword by
hand — the one path no deployment takes. So these tests go through the construction paths
(`_messaging_adapter`, `_whatsapp_webhook`, `MessagingManager._default_adapter`) and through the
environment, the way a `.env` sets the list.

An empty list still means "anyone": that is the owner's decision, because refusing everyone would
silence the production bot on upgrade. What these pin is that it is no longer silent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import typer

from chimera.api.config_api import APPLIES_WHEN, NEXT_LAUNCH, is_editable, read_config
from chimera.cli import main as cli
from chimera.config import Settings
from chimera.server import (
    DiscordAdapter,
    SignalAdapter,
    SlackAdapter,
    TelegramAdapter,
    WhatsAppWebhook,
)
from chimera.server.gateway import InboundMessage
from chimera.server.manager import MessagingManager

_ENVS = {
    "discord": "CHIMERA_DISCORD_ALLOWED_USERS",
    "telegram": "CHIMERA_TELEGRAM_ALLOWED_USERS",
    "slack": "CHIMERA_SLACK_ALLOWED_USERS",
    "signal": "CHIMERA_SIGNAL_ALLOWED_USERS",
    "whatsapp": "CHIMERA_WHATSAPP_ALLOWED_NUMBERS",
}
_TOKENS = {
    "CHIMERA_DISCORD_BOT_TOKEN": "discord-token",
    "CHIMERA_TELEGRAM_BOT_TOKEN": "telegram-token",
    "CHIMERA_SLACK_BOT_TOKEN": "xoxb-bot",
    "CHIMERA_SLACK_APP_TOKEN": "xapp-app",
    "CHIMERA_SIGNAL_API_URL": "http://bridge:8080",
    "CHIMERA_SIGNAL_NUMBER": "+15550000000",
    "CHIMERA_WHATSAPP_ACCESS_TOKEN": "wa-token",
    "CHIMERA_WHATSAPP_PHONE_NUMBER_ID": "PN1",
    "CHIMERA_WHATSAPP_VERIFY_TOKEN": "verify",
}


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    """Settings the way a deployment builds them: from the environment, never by keyword."""
    for key in [*_ENVS.values(), "CHIMERA_WHATSAPP_APP_SECRET"]:
        monkeypatch.delenv(key, raising=False)
    for key, value in {**_TOKENS, **env}.items():
        monkeypatch.setenv(key, value)
    return Settings()


class _Records(logging.Handler):
    """Collects what one chimera logger emits. The `chimera` logger does not propagate once the
    Rich handler is installed, so caplog on the root would see nothing and prove nothing."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def watch_log() -> Iterator[Callable[[str], _Records]]:
    attached: dict[str, _Records] = {}
    levels: dict[str, int] = {}

    def watch(name: str) -> _Records:
        logger = logging.getLogger(name)
        handler = _Records()
        levels[name] = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        attached[name] = handler
        return handler

    yield watch
    for name, handler in attached.items():
        logging.getLogger(name).removeHandler(handler)
        logging.getLogger(name).setLevel(levels[name])


# --- the setting, read the documented way -----------------------------------------------------------


def test_the_allowlist_is_read_from_the_environment_as_comma_separated_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spaces around the ids and a trailing comma are what a hand-edited `.env` looks like."""
    settings = _settings(monkeypatch, CHIMERA_DISCORD_ALLOWED_USERS=" 111 , 222 ,")
    assert settings.discord_allowed_users == ["111", "222"]
    assert settings.telegram_allowed_users == []


# --- every CLI construction path receives the list ------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "adapter_type"),
    [
        ("discord", DiscordAdapter),
        ("telegram", TelegramAdapter),
        ("slack", SlackAdapter),
        ("signal", SignalAdapter),
    ],
)
def test_chimera_serve_hands_each_platform_bot_its_allowlist(
    monkeypatch: pytest.MonkeyPatch, platform: str, adapter_type: type, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(monkeypatch, **{_ENVS[platform]: "owner-1, owner-2"})
    adapter = cli._messaging_adapter(settings, platform)
    assert isinstance(adapter, adapter_type)
    assert adapter.allowed_users == {"owner-1", "owner-2"}
    assert "OPEN" not in capsys.readouterr().out


@pytest.mark.parametrize("platform", ["discord", "telegram", "slack", "signal"])
def test_an_empty_list_keeps_the_bot_open_and_says_so_loudly(
    monkeypatch: pytest.MonkeyPatch, platform: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Today's behaviour, kept — but `chimera serve` names the setting that closes it."""
    settings = _settings(monkeypatch)
    adapter = cli._messaging_adapter(settings, platform)
    assert adapter.allowed_users is None  # None = anyone; an empty SET would be nobody
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert _ENVS[platform] in out


def test_a_missing_token_still_exits_before_any_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    monkeypatch.setattr(settings, "discord_bot_token", None)
    with pytest.raises(typer.Exit):
        cli._messaging_adapter(settings, "discord")


# --- a listed id gets a turn; anyone else gets nothing ---------------------------------------------


def test_a_message_from_an_id_outside_the_list_produces_no_turn(
    monkeypatch: pytest.MonkeyPatch, watch_log: Callable[[str], _Records]
) -> None:
    """Built through the CLI path, then fed one message from the owner and one from a stranger.

    Every adapter calls the route only for a non-None inbound, so None here IS "no turn, no reply".
    """
    log = watch_log("chimera.server.discord")
    settings = _settings(monkeypatch, CHIMERA_DISCORD_ALLOWED_USERS="42")
    bot = cli._messaging_adapter(settings, "discord")

    def message(author: int) -> InboundMessage | None:
        return bot._inbound(  # type: ignore[no-any-return]
            author_id=author, author_is_bot=False, is_self=False, channel_id=7, content="run it"
        )

    owner = message(42)
    assert owner is not None and owner.user == "42"
    assert message(666) is None
    # Ignored silently towards the stranger, but not towards the owner: the id is in the debug log,
    # which is how someone who listed the wrong id finds the right one.
    assert any("666" in line for line in log.messages)


@pytest.mark.parametrize(
    ("platform", "inside", "outside"),
    [
        (
            "telegram",
            {"update_id": 1, "message": {"from": {"id": 42}, "chat": {"id": 9}, "text": "hi"}},
            {"update_id": 2, "message": {"from": {"id": 666}, "chat": {"id": 9}, "text": "hi"}},
        ),
        (
            "slack",
            {"type": "message", "user": "U42", "channel": "C1", "text": "hi"},
            {"type": "message", "user": "U666", "channel": "C1", "text": "hi"},
        ),
        (
            "signal",
            {"envelope": {"source": "+42", "dataMessage": {"message": "hi"}}},
            {"envelope": {"source": "+666", "dataMessage": {"message": "hi"}}},
        ),
    ],
)
def test_the_other_platforms_drop_a_stranger_too(
    monkeypatch: pytest.MonkeyPatch, platform: str, inside: dict[str, Any], outside: dict[str, Any]
) -> None:
    listed = {"telegram": "42", "slack": "U42", "signal": "+42"}[platform]
    bot = cli._messaging_adapter(_settings(monkeypatch, **{_ENVS[platform]: listed}), platform)
    parse = {
        "telegram": "_message_from_update",
        "slack": "_message_from_event",
        "signal": "_message_from_envelope",
    }[platform]
    assert getattr(bot, parse)(inside) is not None
    assert getattr(bot, parse)(outside) is None


# --- the app's messaging manager ------------------------------------------------------------------


def _manager(settings: Settings) -> MessagingManager:
    return MessagingManager(
        settings=settings, backend=object(), model=None, max_steps=1, workspace=Path(".")  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(("platform", "adapter_type"), [("discord", DiscordAdapter), ("telegram", TelegramAdapter)])
def test_the_apps_messaging_toggle_hands_the_bot_the_same_allowlist(
    monkeypatch: pytest.MonkeyPatch, platform: str, adapter_type: type
) -> None:
    """The second construction path. It built from the token alone, like the CLI did."""
    settings = _settings(monkeypatch, **{_ENVS[platform]: "42"})
    adapter = _manager(settings)._default_adapter(platform)
    assert isinstance(adapter, adapter_type)
    assert adapter.allowed_users == {"42"}


def test_the_app_logs_a_warning_when_it_starts_an_open_bot(
    monkeypatch: pytest.MonkeyPatch, watch_log: Callable[[str], _Records]
) -> None:
    log = watch_log("chimera.server.manager")
    adapter = _manager(_settings(monkeypatch))._default_adapter("telegram")
    assert adapter.allowed_users is None
    assert any("CHIMERA_TELEGRAM_ALLOWED_USERS" in line for line in log.messages)


# --- WhatsApp: the same hole, plus an unsigned webhook ---------------------------------------------


class _Gateway:
    def __init__(self) -> None:
        self.turns: list[InboundMessage] = []

    def on_message(self, message: InboundMessage) -> str:
        self.turns.append(message)
        return "reply"


def _payload(sender: str) -> dict[str, Any]:
    return {
        "entry": [
            {"changes": [{"value": {"messages": [{"type": "text", "from": sender, "text": {"body": "hi"}}]}}]}
        ]
    }


def test_the_whatsapp_webhook_answers_only_listed_numbers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Written the way a phone shows it; Meta sends bare digits. Both have to be the same person."""
    settings = _settings(
        monkeypatch,
        CHIMERA_WHATSAPP_ALLOWED_NUMBERS="+55 11 98765-4321",
        CHIMERA_WHATSAPP_APP_SECRET="app-secret",
    )
    gateway = _Gateway()
    hook = cli._whatsapp_webhook(settings, gateway)  # type: ignore[arg-type]
    assert isinstance(hook, WhatsAppWebhook)
    sent: list[str] = []
    monkeypatch.setattr(hook.sender, "send", lambda chat_id, text: sent.append(chat_id) or "ok")

    assert hook.on_message(_payload("15550001111")) == 0
    assert gateway.turns == [] and sent == []  # no turn, no reply to the stranger

    assert hook.on_message(_payload("5511987654321")) == 1
    assert [m.user for m in gateway.turns] == ["5511987654321"]
    assert sent == ["5511987654321"]
    assert "WARNING" not in capsys.readouterr().out


def test_an_open_or_unsigned_whatsapp_webhook_warns_and_still_answers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(monkeypatch)
    gateway = _Gateway()
    hook = cli._whatsapp_webhook(settings, gateway)  # type: ignore[arg-type]
    monkeypatch.setattr(hook.sender, "send", lambda chat_id, text: "ok")
    assert hook.on_message(_payload("15550001111")) == 1  # today's behaviour, kept
    out = capsys.readouterr().out
    assert "CHIMERA_WHATSAPP_ALLOWED_NUMBERS" in out
    assert "CHIMERA_WHATSAPP_APP_SECRET" in out


# --- the settings screen can write and read it ----------------------------------------------------


def test_the_settings_screen_can_save_every_allowlist_and_is_told_when_it_applies() -> None:
    for env in _ENVS.values():
        assert is_editable(env)
        assert APPLIES_WHEN[env] == NEXT_LAUNCH


def test_the_saved_lists_are_read_back_in_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ids, not secrets — a list the owner cannot read back is a list they cannot correct."""
    settings = _settings(monkeypatch, CHIMERA_TELEGRAM_ALLOWED_USERS="42,43")
    allowed = read_config(settings)["messaging"]["allowed_users"]
    assert allowed["telegram"] == ["42", "43"]
    assert allowed["discord"] == []
    assert set(allowed) == set(_ENVS)
