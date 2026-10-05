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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.config import Settings
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
    return str(message.user).strip() in allowed_ids(settings, message.platform)


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
    return set(ids) if ids else None


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
