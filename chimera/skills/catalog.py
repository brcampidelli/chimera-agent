"""The catalogue of installable skill bundles — curated, and honest about what will not work.

A list of names would be easy and would mislead. These skills were written for Claude Code, and
they do not all transfer: some name that tool's own tools in their instructions, some require a
running MCP server or a desktop application, some are macOS-only, and some need heavy local
tooling (LaTeX, ffmpeg, a GPU) before their first line does anything. A catalogue that showed
seventy entries as one undifferentiated list would be advertising sixty working features and
delivering rather fewer, and the person who found that out would find it out after installing.

So every entry carries what it needs and how well it travels, and the surfaces that render the
catalogue show that next to the name rather than behind it. ``Portability.NATIVE`` is the claim
that this bundle works here as written; anything else is a caveat the reader gets before the
install button, not after.

Nothing is vendored. An entry is a *pointer* — repo, path, ref, licence — and installing fetches
from the source. That keeps the licences where they belong (the user installs from the author,
under the author's terms), keeps the skills current instead of frozen at the version somebody
copied, and means this file stays a few kilobytes of data rather than a fork of other people's
work with our name on the directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

__all__ = ["CATALOG", "CatalogEntry", "Portability", "find", "search", "topics"]


class Portability(StrEnum):
    """How well a skill written for another harness travels to this one.

    The distinction that matters is between "needs something installed" and "needs a harness we
    are not" — the first is a shopping list, the second is a wall.
    """

    #: Works as written: prose plus, at most, scripts that run on a normal Python or shell.
    NATIVE = "native"
    #: Works once a listed dependency is present — a binary, a package, an API key.
    NEEDS_SETUP = "needs_setup"
    #: Needs a server or a desktop application running alongside it.
    NEEDS_SERVICE = "needs_service"
    #: Runs only on one operating system.
    OS_LOCKED = "os_locked"
    #: Needs real hardware or a multi-gigabyte install first — a GPU, model weights, full LaTeX.
    #: Kept apart from NEEDS_SETUP because "pip install this" and "have 24GB of VRAM" are not the
    #: same sentence to somebody deciding whether to click.
    NEEDS_HEAVY = "needs_heavy"
    #: Written against Claude Code's own tools or CLI; the instructions name things we do not have.
    NEEDS_ADAPTATION = "needs_adaptation"


@dataclass(frozen=True)
class CatalogEntry:
    """One installable skill, and everything a person needs to decide before installing it."""

    name: str
    description: str
    #: ``owner/repo`` on GitHub.
    repo: str
    #: The directory inside that repo holding ``SKILL.md``.
    path: str
    #: The licence of the source repository, as found there. Empty means we did not find one —
    #: which is not the same as permissive, and the surfaces say so.
    license: str
    portability: Portability
    #: Branch or tag to fetch. The install records the commit it resolved to.
    ref: str = "main"
    #: What must already be on the machine: binaries, packages, services, keys.
    requires: tuple[str, ...] = ()
    #: Grouping for browsing. Free-form, matched against `chimera.skills.skill_md.TOPICS` where
    #: one fits, since a person arriving from an aggregator should recognise the vocabulary.
    topic: str = ""
    #: Said out loud when the portability rating alone would not be enough to decide.
    note: str = ""
    #: MEASURED, unlike `portability`: the tool names this skill's text calls that we answer to
    #: under another name, and the ones nothing here provides. Kept apart from the verdict on
    #: purpose — whether a mention blocks a skill is a judgement, and these are facts.
    uses: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    #: Whoever the upstream credits. Several of these are ports of other people's work and say so
    #: here; carrying the field means the attribution reaches a reader instead of stopping at the
    #: repository it was copied from.
    author: str = ""

    @property
    def homepage(self) -> str:
        return f"https://github.com/{self.repo}/tree/{self.ref}/{self.path}"


#: Where the generated data lives. Beside this module so it ships with the package, and generated
#: rather than typed: `scripts/refresh_skill_catalog.py` derives every field from the skills' own
#: frontmatter, so the file is re-derivable and reviewable in a diff instead of being a list of
#: claims nobody can check.
_DATA = Path(__file__).with_name("catalog.json")


def _load() -> tuple[CatalogEntry, ...]:
    """Read the generated catalogue, or an empty one.

    Empty is a real state, not an error: a build that lost the data file should say it ships no
    catalogue rather than crash on import and take the whole CLI with it.
    """
    try:
        raw = json.loads(_DATA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    out = []
    for item in raw.get("skills", []):
        try:
            out.append(
                CatalogEntry(
                    name=str(item["name"]),
                    description=str(item.get("description") or ""),
                    repo=str(item["repo"]),
                    path=str(item["path"]),
                    license=str(item.get("license") or ""),
                    portability=Portability(item.get("portability") or "native"),
                    ref=str(item.get("ref") or "main"),
                    requires=tuple(str(r) for r in item.get("requires") or ()),
                    topic=str(item.get("topic") or ""),
                    note=str(item.get("note") or ""),
                    author=str(item.get("author") or ""),
                    uses=tuple(str(u) for u in item.get("uses") or ()),
                    missing=tuple(str(m) for m in item.get("missing") or ()),
                )
            )
        except (KeyError, ValueError):
            # One malformed entry must not take the other eighty-one with it.
            continue
    return tuple(out)


CATALOG: tuple[CatalogEntry, ...] = _load()

_BY_NAME = {entry.name: entry for entry in CATALOG}


def find(name: str) -> CatalogEntry | None:
    """The entry with this name, or ``None`` — callers decide what an unknown name means."""
    return _BY_NAME.get(name)


def search(query: str = "", *, topic: str = "") -> list[CatalogEntry]:
    """Entries matching a free-text query and/or a topic, in catalogue order.

    Substring matching over name and description. Deliberately not clever: the catalogue is small
    enough to read, and a scorer that hides an entry a person typed the name of is worse than no
    scorer at all.
    """
    needle = query.strip().lower()
    out = []
    for entry in CATALOG:
        if topic and entry.topic != topic:
            continue
        if needle and needle not in entry.name.lower() and needle not in entry.description.lower():
            continue
        out.append(entry)
    return out


def topics() -> list[str]:
    """The topics that actually have entries — never a menu of empty drawers."""
    seen: dict[str, int] = {}
    for entry in CATALOG:
        if entry.topic:
            seen[entry.topic] = seen.get(entry.topic, 0) + 1
    return sorted(seen)


#: Sources whose licence permits a user to install from them. Recorded per entry rather than
#: assumed: this project is Apache-2.0, and while *pointing* an installer at a copyleft skill is
#: not the same as vendoring it, a person deserves to see the terms before the download.
KNOWN_LICENSES: tuple[str, ...] = (
    "Apache-2.0",
    "MIT",
    "BSD-3-Clause",
    "BSD-2-Clause",
    "ISC",
    "Unlicense",
    "CC0-1.0",
)


def license_is_permissive(license_id: str) -> bool:
    """Whether the licence is one a user can install and adapt without further reading.

    False is not a refusal. It means the surface says "read the licence first" instead of nothing,
    which is the difference between informing somebody and deciding for them.
    """
    return license_id in KNOWN_LICENSES


_ = field  # re-exported dataclass helper kept importable for entry construction in tests
