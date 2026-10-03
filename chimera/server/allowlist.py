"""Who may talk to a bot — the one place that turns settings into an adapter's allowlist.

Every adapter has taken ``allowed_users`` since it shipped (``None`` = anyone), and no construction
path ever passed one: `chimera serve --discord` and the app's Messaging toggle both built the bot
from its token alone. So the bot answered whoever could reach it, with the owner's tools and the
owner's spend, and nothing anywhere said so. Two construction paths (the CLI and
:class:`~chimera.server.manager.MessagingManager`) reading the same settings is how they drift, so
both ask here.

An empty list keeps meaning "anyone". That is the owner's call, not this module's: the production
bot runs on exactly that default, and turning it into "nobody" would silence it on upgrade. What
changes is that the open state is no longer silent — :func:`open_bot_warning` is the sentence both
paths print, and the Settings card shows the same fact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.config import Settings

#: Platform -> the settings attribute holding its allowlist, and the env var that sets it.
ALLOWLIST_FIELDS: dict[str, tuple[str, str]] = {
    "discord": ("discord_allowed_users", "CHIMERA_DISCORD_ALLOWED_USERS"),
    "telegram": ("telegram_allowed_users", "CHIMERA_TELEGRAM_ALLOWED_USERS"),
    "slack": ("slack_allowed_users", "CHIMERA_SLACK_ALLOWED_USERS"),
    "signal": ("signal_allowed_users", "CHIMERA_SIGNAL_ALLOWED_USERS"),
    "whatsapp": ("whatsapp_allowed_numbers", "CHIMERA_WHATSAPP_ALLOWED_NUMBERS"),
}


def allowed_ids(settings: Settings, platform: str) -> list[str]:
    """The ids the owner listed for ``platform``, as written (stripped, blanks dropped)."""
    attr, _env = ALLOWLIST_FIELDS[platform]
    raw = getattr(settings, attr, None) or []
    return [str(item).strip() for item in raw if str(item).strip()]


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
