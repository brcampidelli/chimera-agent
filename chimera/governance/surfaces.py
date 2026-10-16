"""What kind of surface a ``governed_profile`` surface name is — the one place that decides it.

A surface name is ``family`` or ``family:detail``: the bots append the adapter that runs them
(``platform:discord`` from ``chimera serve``, ``app-messaging:telegram`` from the desktop app's
messaging manager), the cron appends the job (``cron:daily-report``), a board lane its agent. Every
rule about surfaces is a rule about the FAMILY, never the full name.

This module exists because three call sites each wrote that rule themselves, and one wrote it wrong:
``openapi_store`` compared the full name against ``{"platform", "app-messaging", "serve"}`` while
``governed_profile`` and ``chat_approval`` tested prefixes. No bot has passed a bare family since the
adapter was appended, so the refusal that keeps the owner's API connectors off an open bot never
fired on one — and nothing said so, because "not refused" and "not a bot" read the same.
"""

from __future__ import annotations

#: The bots: an inbox that answers whoever its allowlist lets through — and an empty allowlist,
#: the shipped default, lets through anyone.
BOT_FAMILIES: frozenset[str] = frozenset({"platform", "app-messaging"})

#: Where a stranger can be on the other end: the bots, and ``chimera serve``'s own chat, which
#: carries the WhatsApp webhook. ``serve-v1`` (the authenticated ``/v1`` gateway) is its own family.
MESSAGING_FAMILIES: frozenset[str] = BOT_FAMILIES | {"serve"}


def surface_family(surface: str) -> str:
    """``"platform"`` for ``"platform:discord"``; a name without ``:`` is its own family."""
    return surface.partition(":")[0].strip()


def is_bot_surface(surface: str) -> bool:
    """A messaging bot's turn, whichever assembly started it and whichever adapter it runs."""
    return surface_family(surface) in BOT_FAMILIES


def is_messaging_surface(surface: str) -> bool:
    """A surface where whoever messages the agent may not be its owner."""
    return surface_family(surface) in MESSAGING_FAMILIES


def bot_platform(surface: str) -> str:
    """The adapter a bot surface names (``"discord"``), or ``""`` for any other surface."""
    return surface.partition(":")[2].strip() if is_bot_surface(surface) else ""
