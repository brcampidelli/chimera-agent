"""Finding a string across a repository.

The agent has `grep_files`; a person looking at the editor has had nothing. Those are not the same
need — a tool call returns a blob for a model to read, a search panel returns hits with line numbers
that you click. So this produces structure, not text.

**ripgrep, through its JSON output.** Parsing `rg`'s human format means guessing where a filename
ends and a match begins, which fails on the first path containing a colon. `--json` emits one object
per event and settles every one of those questions upstream. It also brings the thing that makes a
repository search usable at all: `.gitignore` awareness, so `node_modules` does not drown the result.

**And a fallback that says it is one.** A machine without ripgrep gets a bounded Python walk instead
of an empty result, and the response names which engine answered. The alternative — reporting "not
available" and stopping — is honest but useless, and a silent fallback is useful but dishonest. The
engine field is what makes the third option possible.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from chimera.proc.stdio import resolve_program
from chimera.telemetry import get_logger

_log = get_logger("core.search")

#: Hits returned at most. A search that matches forty thousand lines is a search that needs a better
#: query, and shipping all of them to a browser helps nobody.
MAX_HITS = 500

#: Characters of a matching line that travel. A minified bundle is one line of two megabytes.
MAX_LINE_CHARS = 400

#: Seconds before the search is abandoned. Long enough for a large repository on a cold cache.
TIMEOUT = 30.0

#: Directories the Python fallback never descends into. ripgrep gets this from `.gitignore`; the
#: fallback has no such source, and without a list it spends its whole budget inside `.git`.
_SKIP_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
        ".pytest_cache", ".ruff_cache", "dist", "build", "target", ".next", ".cache", ".tox",
        "site-packages", ".idea", ".vscode", "coverage", ".gradle",
    }
)

#: Files the fallback will not open. Cheap extension check rather than content sniffing, because the
#: point of the cap is to not read them.
_SKIP_SUFFIXES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".7z",
        ".exe", ".dll", ".so", ".dylib", ".pyc", ".pyd", ".class", ".jar", ".woff", ".woff2",
        ".ttf", ".otf", ".mp4", ".mp3", ".wav", ".bin", ".db", ".sqlite", ".lock",
    }
)

#: Files the fallback opens at most, so a monorepo cannot turn a search into a walk of everything.
_FALLBACK_FILE_BUDGET = 20_000


@dataclass(frozen=True)
class Hit:
    """One matching line."""

    path: str  # workspace-relative, forward slashes — the same shape the file tree and editor use
    line: int  # 1-based
    text: str
    #: Byte offsets of the match within `text`, so the UI can highlight rather than re-search — and
    #: re-searching in the browser is how a case-insensitive or regex query gets highlighted wrong.
    start: int
    end: int


@dataclass
class SearchResult:
    """What a search found, and how honestly it found it."""

    hits: list[Hit] = field(default_factory=list)
    #: "ripgrep" or "python". Reported because they are not equivalent: the fallback ignores
    #: `.gitignore`, scans fewer files, and is slower per file.
    engine: str = "ripgrep"
    #: True when the hit cap or the file budget stopped the search early. A capped result that looks
    #: complete is how someone concludes a symbol is unused.
    capped: bool = False
    #: True when the search ran out of time. Distinct from `capped`: one means "too many answers",
    #: the other means "no answer yet".
    timed_out: bool = False
    elapsed_ms: int = 0
    error: str = ""


def _relative(path: str, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return Path(path).as_posix()


def ripgrep_available() -> bool:
    """Whether `rg` resolves on this machine right now."""
    program = resolve_program("rg")
    return program != "rg" or os.path.exists(program)


def _rg_argv(query: str, *, regex: bool, case_sensitive: bool, glob: str) -> list[str]:
    argv = [
        resolve_program("rg"),
        "--json",
        "--line-number",
        "--max-count", str(MAX_HITS),
        # Skip what a person searching source never means: binaries, and files so large they are
        # data rather than code.
        "--max-filesize", "2M",
    ]
    if not regex:
        argv.append("--fixed-strings")
    argv.append("--case-sensitive" if case_sensitive else "--ignore-case")
    # ripgrep skips `node_modules` because a `.gitignore` says so — and a workspace that is not a
    # git repository, or has no ignore file, has nothing saying so. Measured: a bare folder with a
    # `node_modules` in it returns fifty hits from vendored code and pushes the real ones past the
    # cap. Excluding them explicitly also makes the two engines answer the same question, which is
    # the one property a fallback has to have.
    for skip in sorted(_SKIP_DIRS):
        argv += ["--glob", f"!**/{skip}/**"]
    if glob.strip():
        argv += ["--glob", glob.strip()]
    argv += ["--", query, "."]
    return argv


def _parse_rg(line: str, root: Path, hits: list[Hit]) -> None:
    """Turn one `rg --json` event into a hit, ignoring the ones that are not matches."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return
    if event.get("type") != "match":
        return
    data = event.get("data") or {}
    # `text` is absent and `bytes` present when the content is not valid UTF-8. Skipping is right:
    # the panel shows lines, and there is no line to show.
    path = ((data.get("path") or {}).get("text")) or ""
    text = ((data.get("lines") or {}).get("text")) or ""
    if not path or not text:
        return
    submatches = data.get("submatches") or [{}]
    first = submatches[0] if isinstance(submatches, list) and submatches else {}
    hits.append(
        Hit(
            path=_relative(path, root),
            line=int(data.get("line_number") or 0),
            text=text.rstrip("\n")[:MAX_LINE_CHARS],
            start=int(first.get("start") or 0),
            end=int(first.get("end") or 0),
        )
    )


def _search_ripgrep(
    query: str, root: Path, *, regex: bool, case_sensitive: bool, glob: str
) -> SearchResult:
    result = SearchResult(engine="ripgrep")
    started = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 — argv list, never a shell string
            _rg_argv(query, regex=regex, case_sensitive=case_sensitive, glob=glob),
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        result.timed_out = True
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result
    except OSError as exc:  # pragma: no cover - the availability check runs first
        result.error = str(exc)
        return result

    for line in proc.stdout.splitlines():
        if len(result.hits) >= MAX_HITS:
            result.capped = True
            break
        _parse_rg(line, root, result.hits)
    # Exit 1 is ripgrep saying "no matches", which is an answer and not a failure. Anything else
    # with no hits is worth surfacing rather than presenting as an empty repository.
    if proc.returncode not in (0, 1) and not result.hits:
        result.error = (proc.stderr or "").strip()[:400] or f"ripgrep exited {proc.returncode}"
    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    return result


def _search_python(
    query: str, root: Path, *, regex: bool, case_sensitive: bool, glob: str
) -> SearchResult:
    """The fallback. Bounded on purpose, and it reports every bound it hit."""
    result = SearchResult(engine="python")
    started = time.monotonic()
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query if regex else re.escape(query), flags)
    except re.error as exc:
        result.error = f"invalid pattern: {exc}"
        return result

    scanned = 0
    deadline = started + TIMEOUT
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".venv")]
        for name in filenames:
            if time.monotonic() > deadline:
                result.timed_out = True
                result.elapsed_ms = int((time.monotonic() - started) * 1000)
                return result
            if len(result.hits) >= MAX_HITS or scanned >= _FALLBACK_FILE_BUDGET:
                result.capped = True
                result.elapsed_ms = int((time.monotonic() - started) * 1000)
                return result
            path = Path(dirpath) / name
            if path.suffix.lower() in _SKIP_SUFFIXES:
                continue
            if glob.strip() and not path.match(glob.strip()):
                continue
            try:
                if path.stat().st_size > 2_000_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeDecodeError):
                continue  # unreadable or not text; the same files ripgrep skips
            scanned += 1
            for number, line in enumerate(text.splitlines(), start=1):
                match = pattern.search(line)
                if match is None:
                    continue
                # One hit per matching LINE. A `break` here would stop at the first matching line in
                # the file, which is a different rule with the same shape — it passed the
                # one-hit-per-line test for the wrong reason and returned 1 result for a file with
                # seven hundred.
                result.hits.append(
                    Hit(
                        path=_relative(str(path), root),
                        line=number,
                        text=line[:MAX_LINE_CHARS],
                        start=match.start(),
                        end=match.end(),
                    )
                )
                if len(result.hits) >= MAX_HITS:
                    result.capped = True
                    result.elapsed_ms = int((time.monotonic() - started) * 1000)
                    return result
    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    return result


def search(
    query: str,
    workspace: Path,
    *,
    regex: bool = False,
    case_sensitive: bool = False,
    glob: str = "",
) -> SearchResult:
    """Search ``workspace`` for ``query``. Never raises; a failure is a reported one."""
    root = Path(workspace).resolve()
    if not query:
        return SearchResult(hits=[], engine="ripgrep" if ripgrep_available() else "python")
    if not root.is_dir():
        return SearchResult(error="workspace not found")
    engine = _search_ripgrep if ripgrep_available() else _search_python
    try:
        return engine(query, root, regex=regex, case_sensitive=case_sensitive, glob=glob)
    except Exception as exc:  # noqa: BLE001 — a search must not take the server with it
        _log.warning("search failed: %s", exc)
        return SearchResult(error="the search could not run")
