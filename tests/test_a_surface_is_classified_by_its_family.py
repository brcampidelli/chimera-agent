"""A surface name is ``family`` or ``family:detail``, and every rule about it reads the family.

``openapi_store`` compared the full name against the bare families while the bots pass
``platform:discord`` and ``app-messaging:discord``, so its refusal never fired on a real bot. These
tests pin the one classifier, the names the bot assemblies actually build, and that no module keeps a
private list of surface families that could drift from it again.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from chimera.governance.surfaces import (
    BOT_FAMILIES,
    bot_platform,
    is_bot_surface,
    is_messaging_surface,
    surface_family,
)
from chimera.server.chat_approval import chat_answer_path

ROOT = Path(__file__).resolve().parents[1] / "chimera"


@pytest.mark.parametrize(
    "surface",
    ["platform:discord", "platform:telegram", "app-messaging:discord", "app-messaging:whatsapp",
     "platform", "app-messaging"],
)
def test_every_bot_name_is_a_bot_and_a_messaging_surface(surface: str) -> None:
    assert is_bot_surface(surface) and is_messaging_surface(surface)


@pytest.mark.parametrize(
    "surface",
    ["cron:task", "cron:platform", "kanban-agent:platform", "kanban-solve", "mcp", "a2a", "acp",
     "serve-v1", "serve-v1-guarded", "github-issue", "api:turn", "chat", "assist", "tui", "",
     "platformx:discord", "my-platform:discord"],
)
def test_no_other_surface_is_a_bot(surface: str) -> None:
    assert not is_bot_surface(surface)
    assert bot_platform(surface) == ""


def test_serve_is_messaging_but_not_a_bot_and_the_v1_gateway_is_neither() -> None:
    assert is_messaging_surface("serve") and not is_bot_surface("serve")
    assert not is_messaging_surface("serve-v1") and not is_messaging_surface("serve-v1-guarded")


def test_the_platform_is_the_detail_of_a_bot_surface() -> None:
    assert surface_family("app-messaging:discord") == "app-messaging"
    assert bot_platform("platform:telegram") == "telegram"
    assert bot_platform("app-messaging:whatsapp") == "whatsapp"


def test_the_answer_path_rule_reads_the_same_classifier() -> None:
    """Unchanged behaviour, now through the shared rule: a question raised on a WhatsApp bot has no
    chat answer path whichever assembly ran it; a cron job named after a platform is not a bot."""
    assert chat_answer_path("platform:whatsapp") is False
    assert chat_answer_path("app-messaging:telegram") is False
    assert chat_answer_path("app-messaging:discord") is True
    assert chat_answer_path("cron:telegram") is True
    assert chat_answer_path("whatsapp") is False


def test_the_bot_assemblies_build_their_names_from_a_bot_family() -> None:
    """If an assembly renamed its family, the classifier would quietly stop seeing it as a bot."""
    built: set[str] = set()
    for path in (ROOT / "server" / "manager.py", ROOT / "cli" / "commands" / "serve.py"):
        text = path.read_text(encoding="utf-8")
        built |= set(re.findall(r'surface=f"([a-z-]+):\{(?:adapter\.)?platform\}"', text))
    assert built == set(BOT_FAMILIES)


def test_no_module_keeps_its_own_list_of_bot_families() -> None:
    """The defect was two rules for one question. A prefix tuple or a set literal naming a bot family
    anywhere but the classifier is the next pair to diverge."""
    pattern = re.compile(r"""(startswith\(\(?|frozenset\(\{|\{)\s*["'](platform|app-messaging)[:"']""")
    offenders = [
        f"{path.relative_to(ROOT)}:{n}"
        for path in ROOT.rglob("*.py")
        if path.name != "surfaces.py"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []
