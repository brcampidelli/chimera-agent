"""Bring one plugin tree onto this machine from a v1 source, bounded, and run nothing while doing it.

Three sources and no others (ESTUDO-01 §6.2, owner decision of 17/10/2026):

* **local** — a directory on this machine. Copied file by file from the bounded index
  :func:`chimera.plugins.scan.scan` builds, so a link is never followed and never copied;
* **github** — ``owner/repo`` pinned by a full 40-hex commit SHA, fetched through the skill-bundle
  fetcher (:mod:`chimera.skills.bundles`: the same two GitHub hosts, the same retries, the same
  token rule). A commit SHA names bytes; a branch names whatever is there tomorrow;
* **archive** — an ``https://`` download pinned by ``sha256``. The digest is checked BEFORE a single
  entry is unpacked. Only GitHub's archive hosts are accepted (``github.com`` archive links and
  ``codeload.github.com``, following redirects only to GitHub's asset hosts): the digest pins the
  bytes, but an install button must still not be a general "fetch any address" primitive.

A marketplace ``url`` source that names a github.com repository root, and a ``git-subdir`` source on
github.com, both pinned by ``sha``, arrive here as **github** specs (the second with its folder in
``path``): :func:`_fetch_github` lists the tree at ``<sha>:<path>`` and fetches only what is under it.

``npm``, ``command``, unpinned git, git on any other host and every other kind are refused before
anything is fetched —
one is supply-chain surface with nothing to gain, the other runs a command on the host to install.

What lands on disk is DATA. Nothing here imports, executes or marks anything executable; a plugin's
``bin/``, hooks and monitors are copied so the owner can read them, and stay inert (see
:mod:`chimera.plugins.registry`).
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import shutil
import stat
import tarfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from chimera.plugins.marketplace import classify_source
from chimera.plugins.model import Code, PluginSource, Problem, SourceKind
from chimera.plugins.scan import MAX_DEPTH, MAX_ENTRIES, MAX_TOTAL_BYTES, scan

#: A fetched file's cap. The bundle cap (`chimera.skills.bundles.MAX_FILE_BYTES`): a plugin is text,
#: JSON and the scripts its servers call, and a file past this is not something to vouch for.
MAX_FILE_BYTES = 2 * 1024 * 1024
#: Files fetched from GitHub one request each. Higher than one skill bundle (200) because a plugin is
#: several components; far below the scan's entry cap because every file is a request.
MAX_GITHUB_FILES = 400
#: An archive is read whole into memory to be hashed before it is opened, so it gets its own cap.
MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES

_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
#: Where an archive may be asked for, and where its redirect may land. Not added to the bundle
#: fetcher's `_ALLOWED_HOSTS`: that tuple is also the list of hosts a GitHub token is sent to, and an
#: archive is fetched anonymously — the digest, not a credential, is what makes it trustworthy.
_ARCHIVE_HOSTS = ("github.com", "codeload.github.com")
_ARCHIVE_REDIRECT_HOSTS = (
    "codeload.github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
)
_TIMEOUT = 30.0


class FetchError(RuntimeError):
    """A source refused or a fetch that failed. The message is written for a person."""


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """A v1 source, already checked to be pinned. ``path`` is a folder inside a repository, or the
    local directory itself for ``local``."""

    kind: SourceKind
    path: str = ""
    repo: str = ""
    sha: str = ""
    url: str = ""
    sha256: str = ""

    @property
    def ref(self) -> str:
        """What pins it: the commit, the digest, or nothing for a local folder (hashed at install)."""
        return self.sha or self.sha256

    def describe(self) -> str:
        """The spec as ``chimera plugin install`` accepts it, so ``update`` can re-read it."""
        if self.kind is SourceKind.GITHUB:
            return f"github:{self.repo}@{self.sha}" + (f":{self.path}" if self.path else "")
        if self.kind is SourceKind.ARCHIVE:
            return f"{self.url}#sha256={self.sha256}"
        return self.path

    def default_name(self) -> str:
        """The name Claude Code would use for a plugin with no manifest: its folder's."""
        if self.kind is SourceKind.LOCAL:
            return Path(self.path).name
        if self.kind is SourceKind.GITHUB:
            return (self.path.rstrip("/").rsplit("/", 1)[-1] if self.path else self.repo.rsplit("/", 1)[-1])
        tail = urllib.parse.urlparse(self.url).path.rstrip("/").rsplit("/", 1)[-1]
        for suffix in (".tar.gz", ".tgz", ".zip"):
            if tail.endswith(suffix):
                return tail[: -len(suffix)]
        return tail


def _checked(source: PluginSource, problems: list[Problem]) -> None:
    if source.refusal is not None:
        reason = next((p.message for p in problems), source.refusal.value)
        raise FetchError(reason)


def github_spec(repo: str, sha: str, path: str = "") -> SourceSpec:
    """A GitHub source, validated by the same classifier a marketplace entry goes through."""
    problems: list[Problem] = []
    source = classify_source({"source": "github", "repo": repo, "sha": sha}, problems=problems)
    _checked(source, problems)
    inner = path.strip("/")
    if inner and (inner.startswith(".") or "\\" in inner or ".." in inner.split("/") or ":" in inner):
        raise FetchError(f"a folder inside the repository is a plain relative path: {path!r}")
    return SourceSpec(SourceKind.GITHUB, path=inner, repo=repo, sha=sha)


def archive_spec(url: str, sha256: str) -> SourceSpec:
    problems: list[Problem] = []
    source = classify_source({"source": "archive", "url": url, "sha256": sha256}, problems=problems)
    _checked(source, problems)
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host not in _ARCHIVE_HOSTS:
        raise FetchError(
            f"archives are accepted from GitHub's archive hosts only ({', '.join(_ARCHIVE_HOSTS)}), not {host!r}"
        )
    return SourceSpec(SourceKind.ARCHIVE, url=url, sha256=sha256.lower())


def local_spec(path: str | Path) -> SourceSpec:
    folder = Path(path).expanduser()
    if not folder.is_dir():
        raise FetchError(f"not a directory: {str(folder)!r}")
    return SourceSpec(SourceKind.LOCAL, path=str(folder.resolve()))


def parse_source(text: str) -> SourceSpec:
    """Read what a person typed: ``<folder>``, ``github:owner/repo@<sha>[:folder]`` or
    ``https://…#sha256=<hex>``. Anything else — ``npm:``, a bare branch, a URL without a digest — is
    refused with the reason, before any network or disk access."""
    raw = text.strip()
    lowered = raw.lower()
    if lowered.startswith("github:"):
        body = raw[len("github:"):]
        repo, at, rest = body.partition("@")
        if not at:
            raise FetchError("a github source needs a pinned commit: github:owner/repo@<40-hex sha>")
        sha, _, path = rest.partition(":")
        if not _SHA.fullmatch(sha):
            raise FetchError("a github source is pinned by a full 40-character lowercase commit SHA, not a branch or tag")
        return github_spec(repo, sha, path)
    if lowered.startswith("https://"):
        url, hashmark, digest = raw.partition("#")
        digest = digest[len("sha256="):] if digest.lower().startswith("sha256=") else digest
        if not hashmark or not _SHA256.fullmatch(digest):
            raise FetchError("an archive is pinned by its digest: https://…#sha256=<64 hex characters>")
        return archive_spec(url, digest)
    if lowered.startswith(("http://", "npm:", "git+", "ssh://", "git@")):
        raise FetchError("v1 installs only a local folder, github pinned by sha, or an https archive with sha256")
    return local_spec(raw)


# --- writing ---------------------------------------------------------------------------------------


def _target(root: Path, rel: str) -> Path:
    """Where ``rel`` may be written under ``root``, or a refusal — a name from a source is not a path."""
    pure = PurePosixPath(rel)
    if (not rel or "\\" in rel or "\x00" in rel or pure.is_absolute() or ".." in pure.parts
            or (pure.parts and ":" in pure.parts[0]) or len(pure.parts) > MAX_DEPTH + 1):
        raise FetchError(f"refusing a file named {rel!r}")
    base = root.resolve()
    target = (root / Path(*pure.parts)).resolve()
    if not target.is_relative_to(base) or target == base:
        raise FetchError(f"refusing a file that would land outside the plugin: {rel!r}")
    return target


def _write(root: Path, rel: str, data: bytes) -> None:
    target = _target(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create: a second entry with the same name (an archive can carry one) is refused
    # rather than allowed to replace the bytes the first one wrote.
    with open(target, "xb") as handle:
        handle.write(data)


def _copy_local(source: Path, dest: Path) -> list[str]:
    index = scan(source)
    fatal = [p for p in index.problems if p.severity.value == "error"]
    if fatal:
        raise FetchError(f"{fatal[0].where}: {fatal[0].message}")
    written: list[str] = []
    for rel in sorted(index.files):
        if index.files[rel] > MAX_FILE_BYTES:
            raise FetchError(f"{rel} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MiB")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(os.path.join(index.root, *rel.split("/")), flags)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise FetchError(f"{rel} is not a regular file")
            data = handle.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise FetchError(f"{rel} grew past the cap while being copied")
        _write(dest, rel, data)
        written.append(rel)
    return written


def _github_tree(repo: str, path: str, sha: str) -> list[dict[str, Any]]:
    from chimera.skills import bundles

    treeish = f"{sha}:{path}" if path else sha
    url = f"https://api.github.com/repos/{repo}/git/trees/{urllib.parse.quote(treeish)}?recursive=1"
    payload = json.loads(bundles._get(url, limit=16 * 1024 * 1024).decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("tree"), list):
        raise FetchError("the source did not return a file listing")
    if payload.get("truncated"):
        raise FetchError("the listing came back truncated; a partial plugin is not installed")
    return [item for item in payload["tree"] if isinstance(item, dict)]


def _fetch_github(spec: SourceSpec, dest: Path) -> list[str]:
    from chimera.skills import bundles

    items = _github_tree(spec.repo, spec.path, spec.sha)
    blobs: list[dict[str, Any]] = []
    for item in items:
        mode, kind, rel = str(item.get("mode", "")), item.get("type"), str(item.get("path", ""))
        if kind == "tree":
            continue
        if mode == "120000":
            raise FetchError(f"{rel} is a symbolic link; a plugin is installed without links")
        if kind != "blob":
            raise FetchError(f"{rel} is a {kind}; a plugin is installed from files only")
        blobs.append(item)
    if not blobs:
        raise FetchError(f"nothing at {spec.path or '/'} in {spec.repo}@{spec.sha[:12]}")
    if len(blobs) > MAX_GITHUB_FILES:
        raise FetchError(f"more than {MAX_GITHUB_FILES} files")
    written: list[str] = []
    total = 0
    prefix = f"{spec.path}/" if spec.path else ""
    for item in blobs:
        rel = str(item["path"])
        if int(item.get("size") or 0) > MAX_FILE_BYTES:
            raise FetchError(f"{rel} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MiB")
        _target(dest, rel)  # refuse a hostile name before spending a request on it
        quoted = urllib.parse.quote(f"{prefix}{rel}")
        blob = bundles._get(f"https://raw.githubusercontent.com/{spec.repo}/{spec.sha}/{quoted}",
                            accept="*/*", limit=MAX_FILE_BYTES)
        total += len(blob)
        if total > MAX_TOTAL_BYTES:
            raise FetchError(f"the plugin is larger than {MAX_TOTAL_BYTES // (1024 * 1024)} MiB")
        _write(dest, rel, blob)
        written.append(rel)
    return written


class _GitHubRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to GitHub's own archive and asset hosts, over https."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _ARCHIVE_REDIRECT_HOSTS:
            raise FetchError(f"the archive redirected to {parsed.hostname or newurl!r}, which is not a GitHub host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_archive(url: str) -> bytes:
    """One bounded, anonymous GET. A module function so a test replaces the network, not the checks."""
    opener = urllib.request.build_opener(_GitHubRedirects)
    request = urllib.request.Request(url, headers={"User-Agent": "chimera-agent"})  # noqa: S310 -- host checked by archive_spec
    try:
        with opener.open(request, timeout=_TIMEOUT) as response:
            body: bytes = response.read(MAX_ARCHIVE_BYTES + 1)
    except FetchError:
        raise
    except Exception as exc:  # noqa: BLE001 -- shown to a person as a failed download
        raise FetchError(f"could not download the archive: {exc}") from exc
    if len(body) > MAX_ARCHIVE_BYTES:
        raise FetchError(f"the archive is larger than {MAX_ARCHIVE_BYTES // (1024 * 1024)} MiB")
    return body


def _strip_top(names: list[str]) -> str:
    """The single top folder every entry sits in (``repo-<sha>/`` in a GitHub archive), or ""."""
    tops = {name.split("/", 1)[0] for name in names}
    if len(tops) == 1 and all("/" in name for name in names):
        return next(iter(tops)) + "/"
    return ""


def _members(blob: bytes) -> list[tuple[str, bytes]]:
    """Every regular file of a zip or tar archive, refusing links, devices and oversized content."""
    found: list[tuple[str, bytes]] = []
    total = 0

    def keep(name: str, size: int, read: Any) -> None:
        nonlocal total
        if len(found) >= MAX_ENTRIES:
            raise FetchError(f"the archive has more than {MAX_ENTRIES} entries")
        if size > MAX_FILE_BYTES:
            raise FetchError(f"{name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MiB")
        data = read()
        if len(data) > MAX_FILE_BYTES:
            raise FetchError(f"{name} is larger than its header said")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise FetchError(f"the archive unpacks past {MAX_TOTAL_BYTES // (1024 * 1024)} MiB")
        found.append((name, data))

    if blob[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                    raise FetchError(f"{info.filename} is a symbolic link; refused")
                keep(info.filename, info.file_size,
                     lambda info=info: archive.open(info).read(MAX_FILE_BYTES + 1))
        return found
    if blob[:2] == b"\x1f\x8b" or blob[257:262] == b"ustar":
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tar:
            for member in tar.getmembers():
                if member.isdir():
                    continue
                if not member.isfile():
                    raise FetchError(f"{member.name} is a link or a device; refused")
                handle = tar.extractfile(member)
                if handle is None:
                    raise FetchError(f"{member.name} cannot be read")
                keep(member.name, member.size, lambda handle=handle: handle.read(MAX_FILE_BYTES + 1))
        return found
    raise FetchError("the archive is neither a zip nor a tar file")


def _fetch_archive(spec: SourceSpec, dest: Path) -> list[str]:
    blob = _download_archive(spec.url)
    digest = hashlib.sha256(blob).hexdigest()
    # Checked before the archive is opened: what is unpacked is what the digest named, or nothing.
    if not hmac.compare_digest(digest, spec.sha256.lower()):
        raise FetchError(f"sha256 mismatch: expected {spec.sha256.lower()}, the download is {digest}")
    members = _members(blob)
    names = [name for name, _ in members]
    top = _strip_top(names)
    written: list[str] = []
    for name, data in members:
        rel = name[len(top):] if top else name
        if not rel:
            continue
        _write(dest, rel, data)
        written.append(rel)
    if not written:
        raise FetchError("the archive is empty")
    return written


def fetch(spec: SourceSpec, dest: Path) -> list[str]:
    """Put the plugin's files under ``dest`` (created, must not exist). Returns the relative paths.

    On any failure ``dest`` is removed: a half-fetched plugin on disk reads as installed and is not.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=False)
    try:
        if spec.kind is SourceKind.LOCAL:
            return _copy_local(Path(spec.path), dest)
        if spec.kind is SourceKind.GITHUB:
            return _fetch_github(spec, dest)
        if spec.kind is SourceKind.ARCHIVE:
            return _fetch_archive(spec, dest)
        raise FetchError(f"{spec.kind.value} sources are not installable in v1")
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise


def refusal_for(source: PluginSource) -> str | None:
    """Why a marketplace entry's source cannot be installed, in words, or None."""
    if source.refusal is None:
        return None
    if source.refusal is Code.UNPINNED_SOURCE:
        return "the source is not pinned (github, url and git-subdir need sha, an archive needs sha256)"
    if source.refusal is Code.UNSUPPORTED_SOURCE and source.kind in (SourceKind.GIT, SourceKind.GIT_SUBDIR):
        return f"{source.kind.value} sources are installable in v1 only from github.com"
    if source.refusal is Code.UNSUPPORTED_SOURCE:
        return f"{source.kind.value} sources are not installable in v1"
    return f"the source is invalid ({source.refusal.value})"


__all__ = [
    "MAX_ARCHIVE_BYTES",
    "MAX_FILE_BYTES",
    "MAX_GITHUB_FILES",
    "FetchError",
    "SourceSpec",
    "archive_spec",
    "fetch",
    "github_spec",
    "local_spec",
    "parse_source",
    "refusal_for",
]
