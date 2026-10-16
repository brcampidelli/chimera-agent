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
#: carries the WhatsApp webhook. ``serve-v1`` (the ``/v1`` gateway) is its own family, below.
MESSAGING_FAMILIES: frozenset[str] = BOT_FAMILIES | {"serve"}

#: A GitHub issue job (``chimera serve``'s webhook). The task is the issue — title, body and any
#: ``/chimera`` comment — written by whoever can open or comment on one. The webhook checks the
#: signature and the REPOSITORY (``CHIMERA_GITHUB_ISSUE_REPOSITORIES``), never the author, so there is
#: no allowlist that makes the person driving this run the owner.
ISSUE_FAMILY = "github-issue"

#: ``/v1/chat/completions`` on the desktop server. Its guard is the owner's ``CHIMERA_SERVER_TOKEN``,
#: and an unset token — the default — lets every caller through: the owner's only while it is set.
#: Not ``serve-v1-guarded``, which is the app's own chat, the screen the owner sits at.
OWNER_TOKEN_FAMILY = "serve-v1"


def surface_family(surface: str) -> str:
    """``"platform"`` for ``"platform:discord"``; a name without ``:`` is its own family."""
    return surface.partition(":")[0].strip()


def is_bot_surface(surface: str) -> bool:
    """A messaging bot's turn, whichever assembly started it and whichever adapter it runs."""
    return surface_family(surface) in BOT_FAMILIES


def is_messaging_surface(surface: str) -> bool:
    """A surface where whoever messages the agent may not be its owner."""
    return surface_family(surface) in MESSAGING_FAMILIES


def is_issue_surface(surface: str) -> bool:
    """A run whose task a GitHub issue's author wrote — never known to be the owner."""
    return surface_family(surface) == ISSUE_FAMILY


def is_owner_token_surface(surface: str) -> bool:
    """A surface that is the owner's only while ``CHIMERA_SERVER_TOKEN`` guards it."""
    return surface_family(surface) == OWNER_TOKEN_FAMILY


def bot_platform(surface: str) -> str:
    """The adapter a bot surface names (``"discord"``), or ``""`` for any other surface."""
    return surface.partition(":")[2].strip() if is_bot_surface(surface) else ""
