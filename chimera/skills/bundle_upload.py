"""A skill the owner hands to the app — a ``SKILL.md``, a zipped skill folder, or the folder itself.

The catalogue (:mod:`chimera.skills.bundles`) installs from curated pointers. This is the other
door: a skill that is in nobody's catalogue, because somebody wrote it for themselves or found it
somewhere. It lands in the same place and in the same shape — a directory under
``<home>/skills/<name>/`` with a ``bundle.json`` beside it — so everything that already reads
bundles (the on/off switch, the one-line prompt entry, ``skill_view``) reads this one too.

**The posture is the catalogue's, with less to lean on.** A catalogue entry was read by us before it
was listed; an upload was read by nobody we know. So:

* It lands ``pending`` and ``tainted``, always. What the file says about itself is not consulted —
  the same rule ``chimera skills-import`` applies to a card imported by path (#747): a stranger's
  frontmatter does not get to decide whether anyone reads it first.
* An archive is read as hostile input. Entry names are refused if they are absolute, carry a drive
  or a backslash, climb with ``..``, or are names Windows will not create; symlinks, encrypted
  entries and nested archives are refused; every size is checked against the header AND against
  the bytes actually decompressed, because the header is written by the same person as the bomb.
* Only file types a skill plausibly ships are accepted — prose, scripts, data, images, documents,
  fonts. A native executable is not a skill; it is a download with a skill's name on it.
* A name that belongs to a catalogue entry is refused. Otherwise an upload called ``pdf`` would
  show up in the catalogue as the curated ``pdf`` skill, installed and waiting to be switched on.

Nothing here runs anything. Like the catalogue install, receiving files is all it does.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
import zlib
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import yaml
from yaml.composer import ComposerError
from yaml.events import AliasEvent
from yaml.nodes import Node

from chimera.skills.bundles import (
    MAX_DEPTH,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    BundleError,
    BundleExists,
    InstalledBundle,
    _rmtree,
    _safe_target,
    _swap_into,
    bundles_root,
    is_bundle_name,
    recover_aside,
)

#: An archive's central directory is read before anything is decompressed, and a directory listing
#: a million entries is a way to spend memory without spending bytes. Generous against MAX_FILES
#: because folders and ignored junk count here too.
MAX_ARCHIVE_ENTRIES = 1000

#: The one line of an uploaded skill that reaches the system prompt once it is switched on. Kept to
#: a sentence: it is a stranger's text, and a multi-paragraph "description" is a place to hide
#: instructions in the part of the prompt with the owner's standing.
MAX_DESCRIPTION_CHARS = 300

#: What a skill ships, by extension. An allowlist rather than a denylist: the cost of a missing
#: entry is one refused upload with a message naming the file; the cost of a missing denylist entry
#: is an executable on disk under a skill's name.
ALLOWED_SUFFIXES = frozenset({
    # prose
    ".md", ".markdown", ".txt", ".rst",
    # scripts the instructions may tell the agent to run — run by the agent's governed shell, never
    # by installing
    ".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".bash", ".ps1", ".r",
    # data and templates
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".csv", ".tsv", ".xml",
    ".html", ".htm", ".css", ".sql", ".j2", ".jinja", ".tex", ".bib",
    # assets
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".pdf",
    ".docx", ".xlsx", ".pptx", ".ttf", ".otf", ".woff", ".woff2",
})

#: Files with no extension that are still plainly text.
ALLOWED_BARE_NAMES = frozenset({
    "license", "licence", "notice", "copying", "readme", "makefile", ".gitignore", ".gitkeep",
    # Repository housekeeping a skill kept under version control carries; all plain text.
    ".gitattributes", ".editorconfig", "dockerfile",
})

#: Junk that operating systems and version control leave in a folder or an archive. Skipped rather
#: than refused: refusing would fail every skill zipped on a Mac, and none of these is ever part of
#: what a skill does.
#: `__pycache__` and stray `.pyc`/`.pyo` belong here too: any skill whose script has been run once
#: has them, and refusing the whole upload over compiled bytecode nobody asked for (the first
#: version did, naming the `.pyc`) fails exactly the skills that were tried before being shared.
#: Skipped, never installed — bytecode is not text anyone can read before switching it on.
_IGNORED_DIRS = frozenset({"__macosx", ".git", "__pycache__"})
_IGNORED_FILES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
_IGNORED_SUFFIXES = frozenset({".pyc", ".pyo"})

#: Names Windows will not create, with any extension. A skill called `con` or a file called
#: `aux.py` is a failed write on the owner's machine at best.
_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

#: Characters no path part may carry. `:` alone covers drive letters (`C:`) and NTFS alternate
#: streams (`notes.md:hidden`); `\\` is a separator on Windows, so a "file" named `a\\..\\..\\x`
#: is a traversal there and an innocent name everywhere else.
_BAD_CHARS = re.compile(r'[<>:"|?*\\\x00-\x1f]')

#: The validator's phrase list, applied to the one line that reaches the prompt.
_FORBIDDEN = (
    "ignore previous",
    "ignore all previous",
    "rm -rf",
    "exfiltrate",
    "disable safety",
    "reveal the system prompt",
)


def _clean_part(part: str, whole: str) -> None:
    """Refuse one path segment that cannot be written safely on every system we run on."""
    if part in ("", ".", ".."):
        raise BundleError(f"refusing a file named {whole!r}")
    if _BAD_CHARS.search(part):
        raise BundleError(f"refusing a file named {whole!r}: it carries a character paths cannot hold")
    if part != part.rstrip(". "):
        # Windows drops a trailing dot or space, so `a.` and `a` become one file there.
        raise BundleError(f"refusing a file named {whole!r}: it ends in a dot or a space")
    if part.split(".")[0].lower() in _RESERVED:
        raise BundleError(f"refusing a file named {whole!r}: Windows reserves that name")


def _normalise(raw: str) -> str | None:
    """The relative path a file may have inside the skill, ``None`` for junk, or a refusal."""
    if not raw or raw.startswith("/") or raw.startswith("\\"):
        raise BundleError(f"refusing a file named {raw!r}")
    parts = raw.split("/")
    for part in parts:
        _clean_part(part, raw)
    lowered = [p.lower() for p in parts]
    if (
        any(p in _IGNORED_DIRS for p in lowered[:-1])
        or lowered[-1] in _IGNORED_FILES
        or PurePosixPath(lowered[-1]).suffix in _IGNORED_SUFFIXES
    ):
        return None
    name = lowered[-1]
    suffix = PurePosixPath(name).suffix
    if name not in ALLOWED_BARE_NAMES and (not suffix or suffix not in ALLOWED_SUFFIXES):
        raise BundleError(
            f"refusing {raw!r}: a skill ships text, scripts, data, images and documents, "
            f"and {suffix or 'a file with no extension'} is none of those"
        )
    return "/".join(parts)


def _read_zip(data: bytes) -> list[tuple[str, bytes]]:
    """Every regular file in an archive, read within the bounds and never trusting its headers."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise BundleError("that is not a zip archive this app can read") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise BundleError(f"the archive lists more than {MAX_ARCHIVE_ENTRIES} entries")
        out: list[tuple[str, bytes]] = []
        declared = 0
        actual = 0
        for info in infos:
            if info.is_dir():
                continue
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                # A link inside a skill points at whatever the owner's disk has at that path.
                raise BundleError(f"refusing {info.filename!r}: the archive stores it as a link")
            if mode and stat.S_IFMT(mode) and not stat.S_ISREG(mode):
                raise BundleError(f"refusing {info.filename!r}: it is not a regular file")
            if info.flag_bits & 0x1:
                raise BundleError(f"refusing {info.filename!r}: it is encrypted")
            if _normalise(info.filename) is None:
                continue  # junk, skipped before it costs a byte
            if info.file_size > MAX_FILE_BYTES:
                raise BundleError(f"{info.filename} is larger than the {MAX_FILE_BYTES // 1024}KB file limit")
            declared += info.file_size
            if declared > MAX_TOTAL_BYTES:
                raise BundleError(f"the skill is larger than the {MAX_TOTAL_BYTES // 1024 // 1024}MB limit")
            try:
                with archive.open(info) as handle:
                    # One byte past the limit, so a header that understates the size is caught by
                    # what came out rather than believed.
                    blob = handle.read(MAX_FILE_BYTES + 1)
            except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError) as exc:
                raise BundleError(f"could not read {info.filename!r} from the archive: {exc}") from exc
            if len(blob) > MAX_FILE_BYTES:
                raise BundleError(f"{info.filename} is larger than the {MAX_FILE_BYTES // 1024}KB file limit")
            actual += len(blob)
            if actual > MAX_TOTAL_BYTES:
                raise BundleError(f"the skill is larger than the {MAX_TOTAL_BYTES // 1024 // 1024}MB limit")
            out.append((info.filename, blob))
        return out


def _gather(uploads: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Turn what arrived — one zip, one markdown file, or a folder's files — into (path, bytes)."""
    if not uploads:
        raise BundleError("nothing was uploaded")
    if len(uploads) == 1:
        name, data = uploads[0]
        lowered = name.lower()
        if lowered.endswith(".zip"):
            if len(data) > MAX_TOTAL_BYTES:
                raise BundleError(f"the archive is larger than the {MAX_TOTAL_BYTES // 1024 // 1024}MB limit")
            return _read_zip(data)
        if lowered.endswith(".md") and "/" not in name.replace("\\", "/"):
            # A lone markdown file IS the skill, whatever it was called on the owner's disk.
            return [("SKILL.md", data)]
    return list(uploads)


def _layout(entries: list[tuple[str, bytes]]) -> dict[str, bytes]:
    """Validate every path, strip one shared top folder, and refuse what would collide."""
    files: dict[str, bytes] = {}
    for raw, blob in entries:
        rel = _normalise(raw)
        if rel is None:
            continue
        if len(blob) > MAX_FILE_BYTES:
            raise BundleError(f"{rel} is larger than the {MAX_FILE_BYTES // 1024}KB file limit")
        if rel in files:
            raise BundleError(f"{rel!r} appears twice in the upload")
        files[rel] = blob
    if not files:
        raise BundleError("there are no files in the upload")

    # A zipped folder, or a folder picked whole, puts every path under the folder's own name.
    if not any(p.upper() == "SKILL.MD" for p in files):
        tops = {p.split("/", 1)[0] for p in files}
        if len(tops) == 1 and all("/" in p for p in files):
            top = tops.pop()
            files = {p[len(top) + 1 :]: b for p, b in files.items()}

    seen: dict[str, str] = {}
    for rel in files:
        # Case-insensitively unique: on Windows and macOS `Notes.md` and `notes.md` are one file,
        # and the second write would replace the first without a word.
        key = rel.lower()
        if key in seen:
            raise BundleError(f"{seen[key]!r} and {rel!r} are the same file on this system")
        seen[key] = rel
        if rel.count("/") > MAX_DEPTH:
            raise BundleError(f"the skill nests deeper than {MAX_DEPTH} directories")
    if len(files) > MAX_FILES:
        raise BundleError(f"the skill has more than {MAX_FILES} files")
    if sum(len(b) for b in files.values()) > MAX_TOTAL_BYTES:
        raise BundleError(f"the skill is larger than the {MAX_TOTAL_BYTES // 1024 // 1024}MB limit")
    if "bundle.json" in seen:
        # That name is the record this app writes beside the files; a file arriving under it would
        # be the upload describing its own provenance.
        raise BundleError("refusing a file named 'bundle.json': that name is this app's own record")
    skill_md = [p for p in files if p.upper() == "SKILL.MD"]
    if not skill_md:
        raise BundleError("no SKILL.md at the top of the upload — this is not a skill")
    if skill_md[0] != "SKILL.md":
        files["SKILL.md"] = files.pop(skill_md[0])
    return files


def _description(raw: str) -> str:
    """The skill's own description, made safe to stand as one line of a system prompt."""
    from chimera.governance.sanitize import sanitize_untrusted

    line = sanitize_untrusted(" ".join(raw.split()))
    if not line:
        raise BundleError("SKILL.md declares no description — it is the one line the agent would see")
    lowered = line.lower()
    for phrase in _FORBIDDEN:
        if phrase in lowered:
            raise BundleError(f"refusing the description: it contains {phrase!r}")
    if len(line) > MAX_DESCRIPTION_CHARS:
        line = line[: MAX_DESCRIPTION_CHARS - 1].rstrip() + "…"
    return line


#: The frontmatter of an uploaded SKILL.md is a handful of short keys. Bounded before YAML sees it,
#: so the parser is never the thing deciding how much work a stranger's file gets to cause.
MAX_FRONTMATTER_BYTES = 16 * 1024


class _NoAliasLoader(yaml.SafeLoader):
    """``SafeLoader`` that refuses YAML aliases (``*name``) outright.

    ``safe_load`` is safe against code, not against size. An alias is a reference, so seven nested
    levels of ``&a [*a, *a, ...]`` are a few hundred bytes on disk and a tree of ten million
    elements the moment anything walks it — measured on this route before this loader existed: a
    406-byte SKILL.md took 22 s and 874 MB to import, and one level more is ~9 GB. No honest skill
    frontmatter needs a reference to another of its own values, so the feature is refused rather
    than budgeted.
    """

    def compose_node(self, parent: Node | None, index: int) -> Node | None:
        # The PyYAML stubs leave the parser's event methods unannotated; they return bool / Event.
        if self.check_event(AliasEvent):  # type: ignore[no-untyped-call]
            event = self.peek_event()  # type: ignore[no-untyped-call]
            raise ComposerError(None, None, "aliases are not accepted here", event.start_mark)
        return super().compose_node(parent, index)


def _frontmatter(body: str) -> dict[str, object]:
    """The uploaded SKILL.md's frontmatter as a mapping, read strictly — or a refusal.

    The same block :func:`chimera.skills.skill_md.parse_skill_md` would read, but held to what a
    stranger's file may cost: a size bound, no aliases, no recursion past Python's own limit. A
    file with no frontmatter yields ``{}`` and is then refused for having no name.
    """
    stripped = body.lstrip()
    if not stripped.startswith("---"):
        return {}
    end = stripped.find("\n---", 3)
    if end == -1:
        return {}
    raw = stripped[3:end]
    if len(raw.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
        raise BundleError(
            f"the frontmatter of SKILL.md is larger than {MAX_FRONTMATTER_BYTES // 1024}KB — "
            "it holds a name and a sentence"
        )
    try:
        loaded = yaml.load(raw, Loader=_NoAliasLoader)  # noqa: S506 -- a SafeLoader subclass
    except RecursionError as exc:
        raise BundleError("the frontmatter of SKILL.md nests too deeply to read") from exc
    except yaml.YAMLError as exc:
        raise BundleError(f"the frontmatter of SKILL.md is not YAML this app accepts: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 -- a stranger's file must end in a sentence, not a 500
        # The parser is not the only thing that raises: SafeLoader BUILDS the scalars it recognises,
        # and building is plain Python. `name: 111…1` past 4300 digits is `int()` refusing a number
        # that long (ValueError), `description: 2026-99-99` is `date()` refusing the day — neither
        # is a YAMLError, and both reached the route as an unexplained 500.
        raise BundleError(
            f"the frontmatter of SKILL.md is not YAML this app accepts: {type(exc).__name__}"
        ) from exc
    return loaded if isinstance(loaded, dict) else {}


def _text_field(front: dict[str, object], key: str) -> str:
    """One frontmatter value that must be plain text: absent is ``""``, anything else is refused.

    ``str()`` of a list or a mapping is a representation, not the author's words — and it is how
    an expanded structure would have become a "description".
    """
    value = front.get(key)
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise BundleError(f"SKILL.md's {key!r} must be a line of text")
    return str(value)


def _check_name(name: str, home: Path, *, replace: bool) -> None:
    from chimera.skills.catalog import CATALOG

    # `is_bundle_name`: one path segment, lowercase, no dot — so `..`, absolute paths and the
    # `.partial` staging suffix are impossible by construction.
    if not is_bundle_name(name) or name == "unnamed":
        raise BundleError(
            f"SKILL.md must name the skill in its frontmatter as lowercase letters, digits, "
            f"'-' or '_' (2-64 characters, starting with a letter); got {name!r}"
        )
    if name in _RESERVED:
        raise BundleError(f"refusing the name {name!r}: Windows reserves it")
    if name in {e.name.lower() for e in CATALOG}:
        raise BundleError(
            f"{name!r} is the name of a skill in the catalogue — rename yours so the two "
            "cannot be mistaken for each other"
        )
    if (bundles_root(home) / name).exists() and not replace:
        raise BundleExists(f"a skill named {name!r} is already installed — replace it to upload again")


def import_upload(
    uploads: list[tuple[str, bytes]], home: Path, *, replace: bool = False, label: str = ""
) -> InstalledBundle:
    """Install an uploaded skill into ``<home>/skills/<name>/``, pending and tainted.

    ``uploads`` is what arrived: one ``.zip``, one ``.md``, or a folder's files as
    ``(relative path, bytes)``. ``label`` is how the owner named it (a file or folder name), kept
    as the source so "where did this come from" has an answer. Raises :class:`BundleError` with a
    sentence meant for a person, or :class:`BundleExists` when ``replace`` was not asked for.
    """
    from chimera.skills.aliases import foreign_names, missing_names, translated_names

    files = _layout(_gather(uploads))
    # `utf-8-sig`: Notepad and other Windows editors save UTF-8 with a byte-order mark, and a BOM in
    # front of `---` hid the whole frontmatter — the upload was refused for having no name while the
    # name sat on line two. Only the READING drops it; the file is written as it arrived.
    body = files["SKILL.md"].decode("utf-8-sig", errors="replace")
    # Read strictly here rather than through `parse_skill_md`, which is `safe_load` plus `str()` on
    # whatever came back — fine for the library's own cards, an amplifier for a stranger's.
    front = _frontmatter(body)
    name = _text_field(front, "name").strip()
    if is_bundle_name(name):
        # Before the "is this name taken?" question: a previous version a failed swap left aside is
        # still the owner's skill, and must be asked about — not silently replaced, then deleted.
        recover_aside(bundles_root(home) / name)
    _check_name(name, home, replace=replace)
    description = _description(_text_field(front, "description"))
    licence = _text_field(front, "license")

    digest = hashlib.sha256()
    for rel in sorted(files):
        digest.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(files[rel]).digest())

    root = bundles_root(home) / name
    staging = root.with_name(root.name + ".partial")
    if staging.exists():
        _rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        for rel, blob in files.items():
            target = _safe_target(staging, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
        vocabulary = foreign_names(body)
        record = InstalledBundle(
            name=name,
            description=description,
            source=f"upload: {label}" if label else "upload",
            # Content, not a commit: there is no repository, so the bytes themselves are named.
            ref="sha256:" + digest.hexdigest(),
            license=licence.strip()[:80],
            installed_at=datetime.now(UTC).isoformat(timespec="seconds"),
            files=sorted(files),
            status="pending",
            uses=translated_names(vocabulary),
            missing=missing_names(vocabulary),
            origin="upload",
            provenance="tainted",
        )
        (staging / "bundle.json").write_text(
            json.dumps(record.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        _rmtree(staging)
        raise
    try:
        # Old version aside, new one in, old one deleted — or the old one put back. See `_swap_into`.
        _swap_into(staging, root)
    except OSError:
        _rmtree(staging)
        raise
    return record


#: How much of a SKILL.md the screen is handed to read before switching the skill on. The same
#: ceiling `skill_view` gives the agent, so the owner is never shown less than the model would get.
MAX_READ_CHARS = 60_000


def read_skill_md(name: str, home: Path) -> tuple[str, bool] | None:
    """An installed bundle's SKILL.md as text, and whether it was cut — ``None`` when absent.

    For the owner to read BEFORE switching the skill on: the consent the switch records is only
    worth something if what is being consented to was on the screen. ``name`` arrives in a URL, so
    it is held to the bundle-name rule before it touches a path.
    """
    if not is_bundle_name(name):
        return None
    root = bundles_root(home).resolve()
    target = (root / name / "SKILL.md").resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_READ_CHARS:
        return text[:MAX_READ_CHARS], True
    return text, False
