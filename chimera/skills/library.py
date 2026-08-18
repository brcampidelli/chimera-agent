"""The curated skill-card library, as something a program can read.

``skills/`` at the repository root holds the project's official cards — data, not code, each a
``SKILL.md`` of frontmatter plus Trigger/Do/Avoid/Check/Risk. They were reachable in exactly one
way: ``chimera skills-import skills/<name>``, typed by somebody who had the repository checked out
and knew the path. A ``pip install`` puts no ``skills/`` directory anywhere, and the desktop app
never mentioned the library at all — so the surface the README advertises did not exist for anyone
who had not cloned the repo.

This is a *reader*, not a second store. It parses the same files
:func:`chimera.skills.skill_md.parse_skill_md` already understands, and importing one still goes
through ``SkillStore`` exactly as ``skills-import`` does. Nothing here invents a place to keep a
skill; the point is only that the CLI, the API and the app can all now find the one that exists.

Two roots are searched, in this order:

1. ``chimera/_skill_library`` — the packaged copy (wheel and frozen desktop sidecar), mapped there
   by ``hatch_build.py`` the same way the built SPA becomes ``chimera/_desktop_dist``.
2. ``<repo>/skills`` — a source checkout, which is where contributors and ``uv run`` live.

The source fallback insists on a sibling ``pyproject.toml``. Without that check a wheel installed
into a site-packages directory that happens to contain an unrelated ``skills/`` folder would have
had a stranger's markdown parsed and offered as this project's official advice.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from chimera.skills.skill_md import SkillMd, parse_skill_md
from chimera.telemetry import get_logger

_log = get_logger("skills.library")

#: Where a packaged copy lands inside the installed package.
_PACKAGED = "_skill_library"


@lru_cache(maxsize=1)
def library_root() -> Path | None:
    """The directory holding the curated cards, or ``None`` when this build ships without them.

    Cached: this resolves to the same answer for the life of the process, and it is called once per
    card by the detail endpoint.
    """
    here = Path(__file__).resolve().parent.parent  # chimera/
    packaged = here / _PACKAGED
    if packaged.is_dir():
        return packaged
    source = here.parent / "skills"
    if source.is_dir() and (here.parent / "pyproject.toml").is_file():
        return source
    _log.debug("no skill library found next to %s", here)
    return None


def _card_path(root: Path, name: str) -> Path | None:
    """``root/<name>/SKILL.md`` when ``name`` is a real card in ``root``, else ``None``.

    ``name`` arrives from an API path parameter and from a CLI argument, so it is matched against
    the directory listing rather than pasted into a path. Rejecting ``..`` would not have been
    enough on its own — an absolute name, or a symlinked directory planted in the library, both
    read a file outside it while containing no dots at all.
    """
    for entry in root.iterdir():
        if entry.name == name and entry.is_dir() and not entry.is_symlink():
            card = entry / "SKILL.md"
            return card if card.is_file() and not card.is_symlink() else None
    return None


def card_names() -> list[str]:
    """The names of the curated cards, sorted. Empty when the build ships without the library."""
    root = library_root()
    if root is None:
        return []
    return sorted(p.parent.name for p in root.glob("*/SKILL.md"))


def load_card(name: str) -> SkillMd | None:
    """One curated card by name, or ``None`` if there is no such card (or it cannot be read)."""
    root = library_root()
    if root is None:
        return None
    path = _card_path(root, name)
    if path is None:
        return None
    try:
        return parse_skill_md(path.read_text(encoding="utf-8"))
    except OSError as exc:
        _log.warning("curated card %s unreadable: %s", name, exc)
        return None


def load_library() -> list[SkillMd]:
    """Every curated card, ordered by name.

    An unreadable card is skipped, not raised: the library is a directory a user can edit, and one
    bad file must not make the whole list refuse to render — which would look to them exactly like
    "this build has no skills" rather than "one of them is broken".
    """
    cards = []
    for name in card_names():
        card = load_card(name)
        if card is not None:
            cards.append(card)
    return cards
