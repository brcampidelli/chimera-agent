"""Who may talk to a bot — the one place that turns settings into an adapter's allowlist.

Every adapter has taken ``allowed_users`` since it shipped (``None`` = anyone), and no construction
path ever passed one: `chimera serve --discord` and the app's Messaging toggle both built the bot
from its token alone. So the bot answered whoever could reach it, with the owner's tools and the
owner's spend, and nothing anywhere said so. Two construction paths (the CLI and
:class:`~chimera.server.manager.MessagingManager`) reading the same settings is how they drift, so
both ask here.

An empty list keeps meaning "anyone". That is the owner's call, not this module's: a bot already
deployed on that default (the author's own ran on it until 0.64.2 set an allowlist) would go silent
on upgrade if it turned into "nobody". What
changes is that the open state is no longer silent — :func:`open_bot_warning` is the sentence both
paths print, and the Settings card shows the same fact.
"""

from __future__ import annotations

import json
import os
import secrets
import string
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from chimera.config import Settings

if TYPE_CHECKING:
    from chimera.server.gateway import InboundMessage

#: Platform -> the settings attribute holding its allowlist, and the env var that sets it.
ALLOWLIST_FIELDS: dict[str, tuple[str, str]] = {
    "discord": ("discord_allowed_users", "CHIMERA_DISCORD_ALLOWED_USERS"),
    "telegram": ("telegram_allowed_users", "CHIMERA_TELEGRAM_ALLOWED_USERS"),
    "slack": ("slack_allowed_users", "CHIMERA_SLACK_ALLOWED_USERS"),
    "signal": ("signal_allowed_users", "CHIMERA_SIGNAL_ALLOWED_USERS"),
    "whatsapp": ("whatsapp_allowed_numbers", "CHIMERA_WHATSAPP_ALLOWED_NUMBERS"),
}


#: Platform -> the settings that must ALL be set for its bot to start — the same conditions the
#: constructors check (`cli/main.py: _build_messaging_adapter`, `_whatsapp_webhook`; the app's
#: `MessagingManager` reads the first token of discord and telegram).
CONNECTION_FIELDS: dict[str, tuple[str, ...]] = {
    "discord": ("discord_bot_token",),
    "telegram": ("telegram_bot_token",),
    "slack": ("slack_bot_token", "slack_app_token"),
    "signal": ("signal_api_url", "signal_number"),
    "whatsapp": ("whatsapp_access_token", "whatsapp_phone_number_id", "whatsapp_verify_token"),
}


def bot_configured(settings: Settings, platform: str) -> bool:
    """Whether ``platform``'s bot has what it needs to start — a yes/no, never a token.

    The privacy card warned "anyone" for every platform with an empty allowlist, including bots that
    were never set up; an install with only Discord showed `slack: anyone` and `telegram: anyone`,
    which diluted the one warning that mattered."""
    return all(bool(getattr(settings, attr, None)) for attr in CONNECTION_FIELDS[platform])


def allowed_ids(settings: Settings, platform: str) -> list[str]:
    """The ids the owner listed for ``platform``, as written (stripped, blanks dropped)."""
    attr, _env = ALLOWLIST_FIELDS[platform]
    raw = getattr(settings, attr, None) or []
    return [str(item).strip() for item in raw if str(item).strip()]


def phone_digits(number: object) -> str:
    """A phone number reduced to its digits — the form Meta's webhook uses for ``from``.

    The WhatsApp adapter matches its allowlist on this, because an owner writes their number the
    way a phone shows it ("+55 11 98765-4321") and Meta sends bare digits. Kept here so the
    owner rule below and the adapter apply one comparison, not two that drift.
    """
    return "".join(ch for ch in str(number or "") if ch.isdigit())


def same_id(platform: str, listed: str, sender: object) -> bool:
    """Whether ``sender`` is the id ``listed`` on ``platform``, compared as that transport compares.

    WhatsApp compares digits (see :func:`phone_digits`), so an entry that reduces to no digits
    matches nobody; every other platform compares the stripped text exactly.
    """
    if platform == "whatsapp":
        digits = phone_digits(listed)
        return bool(digits) and digits == phone_digits(sender)
    return str(sender).strip() == listed


def is_listed_owner(settings: Settings, message: InboundMessage) -> bool:
    """Whether ``message`` was written by an id the owner listed for its platform: the owner.

    The same rule `chat_approval` applies before it lets a chat answer an approval, and for the
    same reason: a listed id is the only statement the owner has made about who they are on that
    platform. A bot account is never the owner, and with no allowlist NOBODY is, because "anyone
    may talk to it" says nothing about who the owner is. Used for the provenance of a
    "remember that..." (study 30 S30-29), where answering wrongly costs only a label.
    """
    if message.from_bot or message.platform not in ALLOWLIST_FIELDS:
        return False
    # Through `same_id`, the transport's own comparison: a literal one made the owner of a WhatsApp
    # number written "+55 11 98765-4321" a stranger to this rule while the adapter admitted them,
    # so their own "remember that ..." was stored tainted and armed every later run that recalled it.
    return any(
        same_id(message.platform, listed, message.user)
        for listed in allowed_ids(settings, message.platform)
    )


def owner_on(platform: str, settings: Settings, message: InboundMessage) -> bool | None:
    """:func:`is_listed_owner` for a message of ``platform``; ``None`` (no sender) for any other.

    For a gateway that carries a chat bot alongside routes whose ``user`` means nothing — ``serve``
    mounts the WhatsApp webhook on the same gateway as the HTTP ``/chat`` route and the scheduler's
    webhooks. Keyed on the platform the transport stamps: the WhatsApp webhook always writes
    ``"whatsapp"``. An HTTP caller may write it too, but that caller holds the server token, and
    the worst it gets is its own fact labelled ``[unverified]``.
    """
    if message.platform != platform:
        return None
    return is_listed_owner(settings, message)


def allowed_users_for(settings: Settings, platform: str) -> set[str] | None:
    """The adapter's ``allowed_users``: a set when the owner listed anyone, ``None`` (anyone) when not.

    ``None`` rather than an empty set on purpose: every adapter reads an empty set as "nobody", and
    an empty setting has to keep today's behaviour until the owner decides otherwise.
    """
    ids = allowed_ids(settings, platform)
    state = Path(settings.home).expanduser() / "bot-pairings.json"
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
        stored = data.get(platform, [])
        if isinstance(stored, list):
            ids.extend(str(user) for user in stored if str(user).strip())
    except (OSError, ValueError, AttributeError):
        pass
    return set(ids) if ids else None

def home_is_empty(settings: Settings) -> bool:
    """Whether ``home`` holds no state yet. Take this BEFORE the process writes anything there.

    `chimera serve` opens the memory store (``memory.db``) before it builds the bot, so a fresh
    install already has a non-empty home by the time the adapter is made. Asking then classified
    every first run as an upgrade and started the bot open, which is the posture this replaces.
    """
    home = Path(settings.home).expanduser()
    try:
        return not any(home.iterdir())
    except FileNotFoundError:
        return True
    except OSError:
        return False


def is_new_install(
    settings: Settings, platform: str, *, home_was_empty: bool | None = None
) -> bool:
    """Classify a bot launch once: new means no allowlist key and no prior state in ``home``.

    A present-but-empty key is an explicit existing configuration.  A nonempty home directory is
    treated conservatively as prior state so upgrades never change an owner's working bot.
    ``home_was_empty`` is :func:`home_is_empty` taken at process start; without it the home is
    read now, which is only right when nothing has written to it yet.
    """
    _attr, env_name = ALLOWLIST_FIELDS[platform]
    if env_name in os.environ:
        return False

    configured_env_file = Settings.model_config.get("env_file") or ".env"
    env_files = (
        (configured_env_file,) if isinstance(configured_env_file, (str, Path)) else configured_env_file
    )
    for env_file in env_files:
        try:
            for line in Path(env_file).read_text(encoding="utf-8").splitlines():
                candidate = line.partition("=")[0].strip().removeprefix("export ").strip()
                if candidate == env_name:
                    return False
        except OSError:
            continue

    return home_is_empty(settings) if home_was_empty is None else home_was_empty


class PairingFlow:
    """One-time console-issued DM pairing with expiry and bounded guesses."""

    def __init__(
        self, platform: str, home: Path, *, code: str | None = None,
        clock: Callable[[], float] = time.monotonic, max_attempts: int = 5, ttl: float = 600.0,
    ) -> None:
        self.platform = platform
        self._home = Path(home)
        self.code = code or "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        self._clock = clock
        self._expires = clock() + ttl
        self._max_attempts = max_attempts
        self._attempts = 0
        self._locked = False
        self._used = False
        self._lock = threading.Lock()
        self.allowed_users: set[str] = set()
        self._listed_users = False
        self._state = self._home / "bot-pairings.json"
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self._state.read_text(encoding="utf-8"))
            users = data.get(self.platform, [])
            if isinstance(users, list) and users:
                self.allowed_users.update(str(user) for user in users)
                self._listed_users = True
        except (OSError, ValueError, AttributeError):
            pass

    def authorize(self, user: str, text: str) -> bool:
        """Return true for known users; consume valid pairing codes without routing them."""
        with self._lock:
            if user in self.allowed_users:
                return True
            if self._listed_users:
                return False
            if self._used or self._locked or self._clock() >= self._expires:
                return False
            if secrets.compare_digest(text.strip(), self.code):
                self.allowed_users.add(user)
                self._used = True
                self._listed_users = True
                self._persist()
                return False
            self._attempts += 1
            if self._attempts >= self._max_attempts:
                self._locked = True
            return False

    def _persist(self) -> None:
        self._home.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(self._state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[self.platform] = sorted(self.allowed_users)
        temporary = self._state.with_suffix(".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(self._state)


def new_install_refusal(platform: str) -> str:
    """Startup guidance for a new install which has not explicitly listed bot users."""
    _attr, env = ALLOWLIST_FIELDS[platform]
    return (
        f"Refusing to start {platform} on a new install without an allowlist. "
        f"Set {env} to the user ids allowed to talk to this bot."
    )


def open_bot_warning(platform: str) -> str:
    """What `chimera serve` and the app's log say when ``platform`` starts with no allowlist."""
    _attr, env = ALLOWLIST_FIELDS[platform]
    return (
        f"{platform} bot is OPEN: anyone who can message it gets a turn, with your tools and your "
        f"spend. Set {env} to the ids allowed to talk to it."
    )


WHATSAPP_UNSIGNED_WARNING = (
    "whatsapp webhook is UNSIGNED: CHIMERA_WHATSAPP_APP_SECRET is not set, so anyone who knows the "
    "URL can forge a message and make the agent reply to a number of their choosing. Set it to the "
    "Meta app secret."
)
