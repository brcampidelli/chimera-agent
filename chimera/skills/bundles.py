"""Skill *bundles* — the directory-shaped skills of the wider ecosystem, fetched and kept on disk.

Chimera already speaks ``SKILL.md``. What it could not do is hold a skill whose value is not the
prose: Anthropic's published skills, and the community ones that follow them, ship a ``SKILL.md``
next to ``scripts/`` and ``references/`` — the instructions tell an agent to run
``scripts/fill_form.py``, and without that file the instructions are a description of a thing that
is not there. :func:`chimera.skills.skill_md.to_learned` flattens a parsed skill into card fields,
which is right for a *card* (a short behavioural rule that goes in the system prompt) and silently
lossy for a *bundle*. So bundles are a second shape, not a second store of the same shape:

* a **card** is text, lives in ``skills.json``, and is injected into the prompt when it matches;
* a **bundle** is a directory under ``<home>/skills/<name>/``, and what gets injected is only its
  name, its description, and where to read the rest — which is exactly what progressive disclosure
  (:class:`~chimera.skills.skill_md.Disclosure`) was built for. The agent already has file tools;
  pointing it at a path is the whole integration.

**This module downloads code written by other people, so the posture is the feature.**

* Only from the curated :mod:`chimera.skills.catalog`, and only over ``https`` to GitHub. An
  arbitrary URL is not accepted by default: "install a skill" must not be a general-purpose
  "fetch and unpack whatever this address serves".
* Bounded — file count, per-file bytes, total bytes and directory depth. A hostile or merely
  broken source cannot fill a disk.
* Written through :func:`_safe_target`, which refuses anything that would land outside the
  bundle's own directory. Path traversal in an archive is old, and still works on people who
  assume a name from a server is a name.
* Recorded with provenance: the resolved commit SHA, the source URL and the declared licence go
  into ``bundle.json`` beside the files. "Where did this come from" has to be answerable later,
  not remembered.
* Installed **pending**, never active. The existing rule for an imported card is that a skill from
  a stranger has the standing of an instruction from the owner and must be approved first; a
  bundle is that plus executable files, so the same rule applies with more reason. Nothing here
  runs a downloaded script — installing fetches, and that is all it does.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("skills.bundles")

_API = "https://api.github.com/repos/{repo}/contents/{path}"
_USER_AGENT = "chimera-agent"
_TIMEOUT = 20.0

#: Bounds on what one bundle may be. Set from what real skills are, not from a guess: the largest
#: in the catalogue carries 54 reference templates beside its SKILL.md, and several others sit in
#: the forties, so a limit chosen for "a page and a script" would have refused the skills whose
#: whole value is the material they ship. Still bounded — a runaway source stops rather than fills
#: a disk — and the number is high enough that hitting it means something is wrong.
MAX_FILES = 200
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_DEPTH = 4

#: A separate, larger cap for an API listing. It is metadata, not a file, and using the file cap
#: for it is what cut a tree response in half and produced a parse error nowhere near the cause.
_LISTING_BYTES = 16 * 1024 * 1024

#: The only hosts a bundle may be fetched from. Not a general fetcher.
_ALLOWED_HOSTS = ("api.github.com", "raw.githubusercontent.com")


class BundleError(RuntimeError):
    """Install refused or failed. The message is written to be shown to a person."""


@dataclass
class InstalledBundle:
    """What is on disk, and where it came from.

    Kept as a file beside the bundle rather than in a central index: an index and a directory can
    disagree, and when they do the directory is the one that is true. Reading these back is a
    listing of the filesystem, so a bundle deleted by hand simply stops existing.
    """

    name: str
    description: str = ""
    source: str = ""
    repo: str = ""
    path: str = ""
    #: The commit the files actually came from — a branch name says which branch, not which bytes.
    ref: str = ""
    license: str = ""
    installed_at: str = ""
    files: list[str] = field(default_factory=list)
    #: `pending` until a person approves it. Nothing reads a pending bundle into a prompt.
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def bundles_root(home: Path) -> Path:
    """Where installed bundles live. Beside ``skills.json``, not inside it."""
    return Path(home) / "skills"


# ---------------------------------------------------------------------------------------------
# Fetching


def _get(url: str, *, accept: str = "application/vnd.github+json",
         limit: int = MAX_FILE_BYTES) -> bytes:
    """One bounded GET against an allowlisted host."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        # Belt and braces: every URL here is built from catalogue data, and this is what stops a
        # catalogue entry — or a future caller — from turning this into an open fetcher.
        raise BundleError(f"refusing to fetch from {parsed.hostname or url!r}")
    # A token if the environment offers one. Not required — one API call per install fits inside
    # the anonymous ceiling — but somebody installing a dozen skills in a sitting should not have
    # to wait for an hour they were never told about.
    token = os.environ.get("GH_TOKEN", "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()
    headers = {"User-Agent": _USER_AGENT, "Accept": accept}
    if token and parsed.hostname == "api.github.com":
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)  # noqa: S310 -- scheme and host checked above
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 -- as above
            body: bytes = resp.read(limit + 1)
            if len(body) > limit:
                # Refused, not returned short. A truncated read handed back as though it were the
                # whole thing fails somewhere else entirely — this one surfaced as a JSON parse
                # error at character 2097149, three frames from the cap that caused it.
                raise BundleError(f"the response is larger than the {limit // 1024}KB limit")
            return body
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise BundleError(f"not found at the source: {url}") from exc
        if exc.code in (403, 429):
            # The unauthenticated GitHub API allows 60 requests an hour, and a person who has just
            # installed three skills has no way to know that is what happened.
            raise BundleError(
                "GitHub refused the request — most likely its hourly limit for anonymous "
                "downloads. Try again later, or set GITHUB_TOKEN."
            ) from exc
        raise BundleError(f"the source answered {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001 -- surfaced with the URL that failed
        raise BundleError(f"could not reach the source: {exc}") from exc


def _tree(repo: str, path: str, ref: str) -> list[dict[str, Any]]:
    """Every file inside one skill directory, in ONE API call, with paths already relative to it.

    Walking the contents endpoint costs a request per directory and per file, and the anonymous
    ceiling is sixty an hour — the largest skill here ships fifty-four reference files, so that
    arrangement bought a user about one install per hour and then blamed the network. The tree
    endpoint takes ``ref:path`` as its tree-ish, which scopes the answer to the skill: twenty
    entries instead of the repository's eleven thousand, and no prefix to strip afterwards.
    """
    scoped = f"{ref}:{path.strip('/')}"
    url = f"https://api.github.com/repos/{repo}/git/trees/{urllib.parse.quote(scoped)}?recursive=1"
    payload = json.loads(_get(url, limit=_LISTING_BYTES).decode("utf-8"))
    if not isinstance(payload, dict) or "tree" not in payload:
        raise BundleError("the source did not return a file listing")
    if payload.get("truncated"):
        # Installing part of a skill silently is worse than not installing it.
        raise BundleError("the listing came back truncated")
    return [item for item in payload["tree"] if isinstance(item, dict)]


def _resolve_ref(repo: str, ref: str) -> str:
    """The commit a ref points at right now, so provenance names bytes and not a moving branch."""
    try:
        url = f"https://api.github.com/repos/{repo}/commits/{ref}"
        payload = json.loads(_get(url).decode("utf-8"))
        sha = payload.get("sha") if isinstance(payload, dict) else None
        return str(sha) if isinstance(sha, str) and sha else ref
    except BundleError:
        # Not worth failing an install over: the files are already what they are, and a branch
        # name recorded honestly is better than no install.
        return ref


# ---------------------------------------------------------------------------------------------
# Writing


def _safe_target(root: Path, relative: str) -> Path:
    """The path this file may be written to, or a refusal.

    The name comes from a server. Treating it as a path is how an archive writes outside the
    directory it was supposed to stay in.
    """
    if not relative or relative.startswith("/") or ".." in Path(relative).parts:
        raise BundleError(f"refusing a file named {relative!r}")
    target = (root / relative).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise BundleError(f"refusing a file that would land outside the skill: {relative!r}")
    return target


def _download_tree(repo: str, path: str, ref: str, root: Path) -> list[str]:
    """Fetch one skill directory into ``root``, within the bounds."""
    wanted = [item for item in _tree(repo, path, ref) if item.get("type") == "blob"]
    if not wanted:
        raise BundleError(f"nothing at {path!r} in {repo}")
    if len(wanted) > MAX_FILES:
        raise BundleError(f"the skill has more than {MAX_FILES} files")

    prefix = path.strip("/")
    written: list[str] = []
    total = 0
    for item in wanted:
        inner = str(item["path"])  # already relative to the skill directory
        if inner.count("/") > MAX_DEPTH:
            raise BundleError(f"the skill nests deeper than {MAX_DEPTH} directories")
        size = int(item.get("size") or 0)
        if size > MAX_FILE_BYTES:
            raise BundleError(f"{inner} is larger than the {MAX_FILE_BYTES // 1024}KB file limit")
        blob = _get(f"https://raw.githubusercontent.com/{repo}/{ref}/{prefix}/{inner}", accept="*/*")
        total += len(blob)
        if total > MAX_TOTAL_BYTES:
            raise BundleError(f"the skill is larger than the {MAX_TOTAL_BYTES // 1024 // 1024}MB limit")
        target = _safe_target(root, inner)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        written.append(inner)
    return written


# ---------------------------------------------------------------------------------------------
# The operations


def install(entry: Any, home: Path, *, force: bool = False) -> InstalledBundle:
    """Fetch one catalogue entry into ``<home>/skills/<name>/`` and record where it came from.

    Downloads. Does not run anything, and does not activate anything: the bundle lands ``pending``,
    which is the same standing an imported card gets, for the same reason and one more — these
    files include scripts.
    """
    root = bundles_root(home) / entry.name
    if root.exists() and not force:
        raise BundleError(f"{entry.name} is already installed — pass force to replace it")

    staging = root.with_name(root.name + ".partial")
    if staging.exists():
        _rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        sha = _resolve_ref(entry.repo, entry.ref)
        files = _download_tree(entry.repo, entry.path, entry.ref, staging)
        if not any(f.upper() == "SKILL.MD" for f in files):
            # Without it there is no skill here, whatever else was downloaded.
            raise BundleError("no SKILL.md at that path — this is not a skill directory")
        record = InstalledBundle(
            name=entry.name,
            description=entry.description,
            source=f"https://github.com/{entry.repo}/tree/{entry.ref}/{entry.path}",
            repo=entry.repo,
            path=entry.path,
            ref=sha,
            license=entry.license,
            installed_at=datetime.now(UTC).isoformat(timespec="seconds"),
            files=sorted(files),
            status="pending",
        )
        (staging / "bundle.json").write_text(
            json.dumps(record.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        # A half-downloaded skill on disk is worse than none: it reads as installed and is not.
        _rmtree(staging)
        raise

    if root.exists():
        _rmtree(root)
    staging.rename(root)
    return record


def installed(home: Path) -> list[InstalledBundle]:
    """What is on disk, read from the disk. A directory with no ``bundle.json`` is still reported —
    it exists, and pretending otherwise would hide a skill that is in the way of installing one."""
    root = bundles_root(home)
    if not root.is_dir():
        return []
    out: list[InstalledBundle] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.endswith(".partial"):
            continue
        meta = child / "bundle.json"
        if meta.is_file():
            try:
                raw = json.loads(meta.read_text(encoding="utf-8"))
                known = {f.name for f in fields_of(InstalledBundle)}
                out.append(InstalledBundle(**{k: v for k, v in raw.items() if k in known}))
                continue
            except Exception as exc:  # noqa: BLE001 -- a bad record must not hide the directory
                _log.warning("unreadable bundle.json in %s: %s", child.name, exc)
        out.append(InstalledBundle(name=child.name, description="", status="unknown"))
    return out


def remove(name: str, home: Path) -> bool:
    """Delete an installed bundle. Returns False if there was nothing by that name."""
    root = bundles_root(home) / name
    if not root.is_dir():
        return False
    _safe_target(bundles_root(home), name)  # a name, not a path — never `../../something`
    _rmtree(root)
    return True


#: What an installed bundle can be. Three states, not two: a bundle nobody has looked at yet and
#: one somebody read and deliberately turned off are different facts, and collapsing them would
#: lose the only record that a decision was made.
STATUSES = ("pending", "active", "inactive")


def set_status(name: str, home: Path, status: str) -> bool:
    """Turn a bundle on or off. Returns False if there is nothing by that name.

    A switch rather than a one-way approval: keeping a skill on disk while it is off is the normal
    case — you install several, try them, and leave two running. Making "off" mean "uninstall"
    would charge a download for every change of mind.
    """
    if status not in STATUSES:
        raise BundleError(f"unknown status {status!r}")
    meta = bundles_root(home) / name / "bundle.json"
    if not meta.is_file():
        return False
    raw = json.loads(meta.read_text(encoding="utf-8"))
    raw["status"] = status
    meta.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


def active(home: Path) -> list[InstalledBundle]:
    """Only the bundles switched on. This is what may reach a prompt."""
    return [b for b in installed(home) if b.status == "active"]


def context_lines(home: Path) -> list[str]:
    """One line per active bundle: what it is, and where to read the rest.

    Level 1 of progressive disclosure and nothing more. The body of a skill runs to hundreds of
    lines and several ship dozens of reference files; carrying that in every prompt would cost
    more than the skills are worth. The agent has file tools — a name, a sentence and a path is
    the whole integration, and it reads the procedure at the moment it decides to use it.
    """
    out = []
    for bundle in active(home):
        where = bundles_root(home) / bundle.name / "SKILL.md"
        out.append(f"- {bundle.name}: {bundle.description} (read {where} before using it)")
    return out


def fields_of(cls: type) -> Any:
    from dataclasses import fields as _fields

    return _fields(cls)


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
