"""Concrete importers for Hermes Agent, OpenClaw and Claude (Claude Code's memory files)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from pathlib import Path

from chimera.core.redact import redact
from chimera.memory.manager import MemoryManager
from chimera.memory.models import MemoryItem, project_key
from chimera.migration.base import DirectoryImporter, Importer, MigrationResult


class HermesImporter(DirectoryImporter):
    """Import from a Hermes Agent home (Python; config.yaml, skills/, MEMORY.md)."""

    source = "hermes"
    config_files = ("config.yaml", "config.yml")
    skills_dirs = ("skills",)
    memory_candidates = ("MEMORY.md", "USER.md", "memories/MEMORY.md", "memories/USER.md")
    model_keys = (("model", "default"),)


class OpenClawImporter(DirectoryImporter):
    """Import from an OpenClaw home (TS; config.json, skills/, MEMORY.md)."""

    source = "openclaw"
    config_files = ("config.json", "openclaw.json", "config.yaml")
    skills_dirs = ("skills",)
    memory_candidates = ("MEMORY.md", "memory.md")
    model_keys = (("model",), ("defaultModel",), ("model", "default"))


#: A memory file larger than this is not a list of facts someone wrote; it is skipped and said so.
_MAX_FILE_BYTES = 512 * 1024
#: Enough for any real memory directory, and a ceiling on a path pointed at the wrong place.
_MAX_FILES = 500
_MAX_CANDIDATES = 5000
#: A "fact" longer than this is a pasted document. Skipped rather than cut: half a sentence stored as
#: a fact says something its author did not.
_MAX_FACT_CHARS = 2000

_FRONTMATTER = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_LIST_MARKER = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_RULE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
#: The line under a setext heading (``Title`` then ``=====``). A heading is not a fact, and neither is
#: the row of punctuation that makes one.
_SETEXT = re.compile(r"^(?:=+|-+)$")
_BOM = "\ufeff"


def _without_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    """``line`` with every HTML comment removed, and whether a comment is still open at its end.

    Line by line with a carried state, the way code fences are tracked, because a comment spans
    lines: skipping only the line that STARTS with ``<!--`` let the hidden lines inside it, and the
    closing ``-->``, through as facts — text its author hid from the rendered note.
    """
    kept = ""
    rest = line
    while rest:
        if in_comment:
            end = rest.find("-->")
            if end < 0:
                return kept, True
            rest, in_comment = rest[end + 3 :], False
        else:
            start = rest.find("<!--")
            if start < 0:
                return kept + rest, False
            kept, rest, in_comment = kept + rest[:start], rest[start + 4 :], True
    return kept, in_comment


def claude_project_slug(path: str) -> str:
    """The folder name Claude Code files a project's notes under: every non-alphanumeric is ``-``.

    ``C:\\Users\\me\\My App`` is ``projects/C--Users-me-My-App``. Only ever compared, never parsed
    back into a path: the mapping loses characters, so the way to find which folder a slug belongs
    to is to compute the slug of each folder this program knows and look for it.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def registered_project_keys(chimera_home: Path) -> list[str]:
    """The folders the owner registered (the desktop's project list), as memory files them."""
    from chimera.core.code_projects import CodeProjectRegistry

    rows = CodeProjectRegistry(Path(chimera_home) / "code_projects.json").entries()
    return [key for key in (project_key(row.path) for row in rows) if key]


def facts_from_markdown(text: str) -> list[str]:
    """The statements a person wrote in a Claude memory file, one per paragraph or list item.

    Claude keeps memory as Markdown: ``CLAUDE.md`` is headed prose and lists, and each file under
    ``memory/`` opens with YAML front matter (``name`` / ``description`` / ``type``) that is metadata
    about the note, not a fact about the owner. So the front matter, headings, code fences, tables,
    rules, setext headings (the text AND its ``===`` / ``---`` underline) and HTML comments (every
    line of a multi-line one) are dropped, and a link keeps its text (``[Idioma](idioma.md)`` is the
    word "Idioma", not a file path).

    A fact is a Markdown BLOCK, not a physical line. Notes are hard-wrapped at ~100 columns, and
    reading each line as a fact cut ``- Never push to main`` / ``without a review from the owner.``
    into two "facts", the first of which says the opposite of what was written — the very thing
    ``_MAX_FACT_CHARS`` refuses to do by truncation. So a line that does not open a new block (a
    list item, a quote after non-quote text, a heading, a table, a fence, a rule, a blank) is the
    continuation of the one above and is joined to it with a space, as Markdown renders it.

    A leading byte-order mark is removed first. Windows editors save one, and with it in front the
    front matter no longer starts at ``---`` — so ``name:``, ``description:`` and ``type:`` each
    came out as a "fact".
    """
    text = _FRONTMATTER.sub("", text.replace("\r\n", "\n").removeprefix(_BOM), count=1)
    facts: list[str] = []
    block: list[str] = []
    #: What the open block is: plain paragraph text (which an underline turns into a heading) and/or
    #: a quote (whose next ``>`` line continues it rather than opening a new block).
    plain = quote = False

    def close() -> None:
        joined = " ".join(block).strip()
        if len(joined) >= 3:
            facts.append(joined)
        block.clear()

    in_fence = in_comment = False
    for raw in text.splitlines():
        # Inside a comment a fence marker is hidden text too, not a fence.
        if not in_comment and raw.strip().startswith(("```", "~~~")):
            close()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        visible, in_comment = _without_comments(raw, in_comment)
        line = visible.strip()
        if _SETEXT.match(line) and block and plain:
            block.clear()  # the text above was a heading, not a fact
        if not line or line.startswith(("#", "|")) or _RULE.match(line) or _SETEXT.match(line):
            close()
            continue
        is_quote = line.startswith(">")
        inner = line.lstrip(">").strip()
        item = bool(_LIST_MARKER.match(inner))
        if item or not block or (is_quote and not quote):
            close()
            plain, quote = not (is_quote or item), is_quote
        inner = _LIST_MARKER.sub("", inner)
        block.append(_LINK.sub(lambda m: m.group(1), inner).replace("**", "").strip())
    close()
    return facts


class ClaudeImporter(Importer):
    """Candidate facts from Claude's memory: ``CLAUDE.md`` and the ``memory/*.md`` notes.

    Pointed at ``~/.claude`` it reads the global ``CLAUDE.md`` and every project's auto-memory
    (``projects/*/memory/*.md``); pointed at a project folder it reads that folder's ``CLAUDE.md``
    and ``memory/``. Nothing else, and in particular never ``settings.json`` — it holds environment
    variables and keys, and a memory import has no business opening it.

    What comes out is CANDIDATES, and three properties make them that rather than memory:

    * ``scan`` writes nothing. The preview is the default everywhere this is reachable.
    * every fact is ``kind="semantic"``, never ``persona``. A persona fact is read into the system
      prompt of every conversation as who the owner is; a sentence from another tool's notes does
      not become that by being imported. The owner can add it as persona themselves.
    * every fact is ``provenance="tainted"`` with ``source="claude"``. The trust label in this code
      base is binary — some places check ``== "tainted"`` and others ``== "clean"`` — so a third
      value such as ``"imported"`` would read as unverified in one place and pass as clean in the
      other. Tainted is what recall already labels ``[unverified]``; the source says where it came
      from.

    Each fact is passed through :func:`redact` here, not only on the way into the store, so the
    preview itself never shows a key that was sitting in a note.

    **Where a fact applies.** A note under ``projects/<slug>/memory/`` is about ONE repository
    ("PassaPro uses hue 185"). Filed with no project it would be recalled in every conversation in
    every folder, so it is filed under that repository when ``projects`` (the folders the owner has
    registered) holds the one whose slug matches — and otherwise it stays everywhere and SAYS so:
    ``metadata["claude_project"]`` keeps the slug, and the preview shows the scope beside each fact.
    A folder read directly is treated the same way, whichever it is: ``~/.claude/projects/<slug>``
    itself, or a repository (its ``CLAUDE.md`` belongs to it when it is registered, and is flagged
    when it is not). Only the global ``~/.claude/CLAUDE.md`` and ``memory/`` belong everywhere
    without a flag, which is what they are for.
    """

    source = "claude"

    def __init__(self, home: Path, *, projects: Iterable[str] = ()) -> None:
        super().__init__(home)
        self.projects: tuple[str, ...] = tuple(projects)

    def _is_claude_root(self) -> bool:
        """Whether the folder is Claude's global home (``~/.claude``), whose own notes apply everywhere.

        The real one, or a folder laid out like it (a ``.claude`` holding ``projects/``) — a copy, or
        the home of another account. A repository's own ``.claude/`` has no ``projects/``.
        """
        home = self.home.expanduser().resolve()
        if home == (Path.home() / ".claude").resolve():
            return True
        return home.name == ".claude" and (home / "projects").is_dir()

    def _scope(self, rel: str) -> tuple[str | None, str]:
        """``(project, origin)`` for a file under the folder: the project its facts are filed under
        (``None`` = everywhere), and the Claude slug of the repository it came from ("" for a note
        of the global home, the only notes meant for every folder).

        ``origin`` set with ``project`` ``None`` is what the preview shows as a warning and what
        "Select all new" leaves out. Before, it was set only for ``projects/<slug>/`` RELATIVE to the
        folder chosen, so choosing ``~/.claude/projects/<slug>`` itself (where a project's
        ``memory/`` actually is) or an unregistered repository gave its notes the plain "everywhere"
        badge — one repository's notes recalled in every conversation in every folder, unflagged.
        """
        # Lower-cased on both sides: Windows hands out the drive letter in either case.
        by_slug = {claude_project_slug(key).lower(): key for key in self.projects}
        parts = rel.split("/")
        if len(parts) >= 3 and parts[0] == "projects":
            return by_slug.get(parts[1].lower()), parts[1]
        if self._is_claude_root():
            return None, ""
        home = self.home.expanduser().resolve()
        if home.parent.name == "projects":  # ~/.claude/projects/<slug>, chosen directly
            return by_slug.get(home.name.lower()), home.name
        # A repository; its own `.claude/` (where Claude also reads a CLAUDE.md) belongs to it too.
        here = project_key(home.parent if home.name == ".claude" else home) or ""
        return (here if here in self.projects else None), claude_project_slug(here)

    def _memory_paths(self) -> tuple[list[Path], list[str]]:
        """The files to read, in a stable order, and a note for each one refused."""
        home = self.home.resolve()
        found: list[Path] = []
        for pattern in ("CLAUDE.md", "memory/*.md", "projects/*/memory/*.md"):
            found += sorted(self.home.glob(pattern))
        paths: list[Path] = []
        notes: list[str] = []
        seen: set[Path] = set()
        for path in found:
            # A link is not followed: a note symlinked to ~/.ssh/id_rsa would otherwise be read as
            # facts and shown on screen. The same rule `_taint_imported_skills` keeps for skills.
            if path.is_symlink() or not path.resolve().is_relative_to(home) or not path.is_file():
                notes.append(f"skipped {path.name}: a link or not a regular file")
                continue
            real = path.resolve()
            if real in seen:  # case-insensitive filesystems: one file, two spellings
                continue
            seen.add(real)
            if path.stat().st_size > _MAX_FILE_BYTES:
                notes.append(f"skipped {path.name}: larger than {_MAX_FILE_BYTES // 1024} KB")
                continue
            paths.append(path)
            if len(paths) >= _MAX_FILES:
                notes.append(f"stopped at {_MAX_FILES} files")
                break
        return paths, notes

    def _scan_items(self) -> tuple[list[MemoryItem], list[str], list[str]]:
        paths, notes = self._memory_paths()
        files = [p.relative_to(self.home).as_posix() for p in paths]
        items: list[MemoryItem] = []
        seen: set[str] = set()
        too_long = 0
        for path, rel in zip(paths, files, strict=True):
            text = path.read_text(encoding="utf-8", errors="replace")
            project, slug = self._scope(rel)
            for fact in facts_from_markdown(text):
                if len(fact) > _MAX_FACT_CHARS:
                    too_long += 1
                    continue
                content = redact(fact)
                norm = " ".join(content.lower().split())
                if norm in seen:  # the same line in two notes is one candidate
                    continue
                seen.add(norm)
                items.append(
                    MemoryItem(
                        id=uuid.uuid4().hex,
                        kind="semantic",
                        content=content,
                        source=self.source,
                        provenance="tainted",
                        project=project,
                        metadata={"file": rel, "imported": True, "claude_project": slug},
                    )
                )
                if len(items) >= _MAX_CANDIDATES:
                    notes.append(f"stopped at {_MAX_CANDIDATES} candidate facts")
                    return items, files, notes
        if too_long:
            notes.append(f"skipped {too_long} fact(s) longer than {_MAX_FACT_CHARS} characters")
        return items, files, notes

    def skill_sources(self) -> dict[str, Path]:
        # Claude's skills are a separate decision with its own pending/approve path (skills-import);
        # this importer is memory only.
        return {}

    def memory_items(self) -> list[MemoryItem]:
        return self._scan_items()[0]

    def scan(self) -> MigrationResult:
        items, files, notes = self._scan_items()
        if not files:
            notes.append("no CLAUDE.md or memory/*.md found here")
        return MigrationResult(
            source=self.source,
            home=str(self.home),
            dry_run=True,
            memory_files=files,
            candidates=[item.content for item in items],
            notes=notes,
        )

    def apply(
        self,
        target_home: Path,
        *,
        memory_manager: MemoryManager | None = None,
        only: set[str] | None = None,
    ) -> MigrationResult:
        """Merge the candidates into memory — all of them, or just the ones named in ``only``.

        Overridden because the inherited ``apply`` also writes ``imported/<source>/config.json`` and
        a skills folder, and there is no config or skill being imported here. ``target_home`` is
        accepted for the shared signature and unused: the only write is into ``memory_manager``.

        ``only`` is the reviewed selection, matched against the candidates' content exactly as the
        preview showed it. A string that is not a candidate is ignored rather than written — so the
        route that takes a selection cannot be used to file arbitrary text as "imported from Claude".
        """
        result = self.scan()
        result.dry_run = False
        if memory_manager is None:
            return result
        items = self.memory_items()
        if only is not None:
            items = [item for item in items if item.content in only]
        result.memory_merged = memory_manager.merge(items)
        result.notes.append(f"merged {len(items)} memory item(s): {result.memory_merged}")
        return result


_IMPORTERS: dict[str, type[Importer]] = {
    HermesImporter.source: HermesImporter,
    OpenClawImporter.source: OpenClawImporter,
    ClaudeImporter.source: ClaudeImporter,
}


def available_sources() -> list[str]:
    return sorted(_IMPORTERS)


def get_importer(source: str, home: Path) -> Importer:
    """Return an importer for ``source`` rooted at ``home``."""
    try:
        importer_cls = _IMPORTERS[source]
    except KeyError as exc:
        raise ValueError(
            f"unknown migration source {source!r}; available: {available_sources()}"
        ) from exc
    return importer_cls(home)
