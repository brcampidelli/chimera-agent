"""Opening a pull request from a workspace: what is ready, and the one push it makes (study 29, P8.1).

Two callers, one implementation. The Code screen's Git panel shows :func:`readiness` and opens the
pull request when the owner presses the button on it; the agent's ``open_pull_request`` tool
(`chimera/tools/pull_request.py`) shows the same facts on an approval card and opens it only on the
owner's yes. Neither owns any of the rules below, so the two cannot drift into different ideas of
what "never the default branch" means.

The rules, each a property of the code rather than of the caller:

- **Never the default branch.** The branch pushed is refused when it is the base, when it is the
  remote's default (``origin/HEAD``), and when it is ``main`` or ``master`` whatever the default is
  (a git-flow repository's default is ``develop`` and its production branch is ``main``). The
  refspec names its target (``<sha>:refs/heads/<branch>``), so no ``push.default`` setting can send
  it anywhere else.
- **The destination shown is the destination pushed to.** The card shows origin's PUSH URL (``git
  remote get-url --push``, which applies ``pushurl`` and ``pushInsteadOf``), and the push goes to
  that URL, literally, not to the name ``origin``: a ``pushurl`` set between the card and the
  answer is caught by comparing it with the URL that was shown. A push URL that is not the same
  repository as the fetch URL is refused — the card would name one repository and the code would
  land in another — and so is a remote with more than one push URL.
- **A branch that already exists on origin is said to be updated.** The card names the commit the
  remote branch is at now, and a remote branch that moved after the card is refused, not updated.
- **Never a force push.** No ``--force``, no ``--force-with-lease``, no ``+`` on the refspec. A
  remote that has moved on refuses the push, and the refusal is the answer.
- **What was reviewed is what is pushed.** :func:`open_pull_request` takes the commit the owner saw
  (``expect_head``) and pushes THAT commit, by hash. A branch that moved between the card and the
  answer is refused, not pushed at its new tip.
- **No shell, anywhere.** git and gh get argument lists. The title rides as one ``--title=<text>``
  element, so a title that starts with ``-`` cannot become a flag; the body goes over stdin
  (``--body-file=-``), so it has no length limit from a command line and is never parsed by one.
- **The token never passes through here.** gh keeps its own credentials. ``gh auth status`` is asked
  for its exit code only: its text names the account and a masked token, and none of it is shown.
  Anything git or gh prints is passed through :func:`redact` first, because an ``origin`` URL can
  carry a credential (``https://user:token@host/...``) and both tools echo it on failure.
- **No prompts.** ``GIT_TERMINAL_PROMPT=0`` and ``GH_PROMPT_DISABLED=1``: a question drawn on a
  console nobody can see blocks a request thread until its timeout, which is how this codebase has
  lost requests before (`chimera/sandbox/confirm.py`).
- **No repository hook runs on the owner's yes.** The push runs with ``--no-verify`` and
  ``core.hooksPath`` pointed at an empty directory (``pre-push`` and ``reference-transaction``),
  and every git call here runs with ``core.fsmonitor=false`` (an fsmonitor is a command ``git
  status`` would start). A run with a shell in a sandbox that mounts the workspace can write
  ``.git/hooks``; "open this pull request" must not mean "run what the agent left there, on the
  host".

``gh`` is looked up on PATH only — never in the current directory, where Windows' search looks
first and where the CLI surfaces run with the workspace — and refused when it resolves to a
``.bat`` or ``.cmd``: Windows runs a batch file through ``cmd.exe``, which re-parses its arguments,
and the title and the branch are text the agent wrote. The real gh is an executable on every
platform it ships for.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from chimera.core.worktree import is_git_repo

_MAX_OUTPUT = 4000
_MAX_ERR = 500
#: GitHub refuses a title over 256 characters and a body over 65 536; refused here first, in words.
MAX_TITLE = 256
MAX_BODY = 60_000
_MAX_COMMITS = 20
_TIMEOUT = 120.0

#: Branch names that are never pushed, whatever the remote says its default is.
_USUAL_DEFAULTS = frozenset({"main", "master"})

#: Why a workspace is not ready, as words the screen translates and the tool turns into a sentence.
#: Ordered as they are checked: the first that holds is the one reported.
REASONS = (
    "not_repo",
    "detached",
    "bad_branch",
    "no_origin",
    "push_elsewhere",
    "no_base",
    "default_branch",
    "nothing_ahead",
    "no_gh",
    "gh_signed_out",
    "gh_unknown_host",
    "remote_unreachable",
)

_SENTENCES = {
    "not_repo": "the workspace is not a git repository",
    "detached": "HEAD is detached; commit the work on a branch first",
    "no_origin": "the repository has no remote named origin",
    "push_elsewhere": (
        "origin pushes to a different repository than it fetches from (remote.origin.pushurl, "
        "url.*.pushInsteadOf, or more than one push URL); the pull request would not land where "
        "the card says"
    ),
    "no_base": "the default branch of origin could not be read; name a base branch",
    "default_branch": "the branch is the default branch; commit the work on another branch first",
    "nothing_ahead": "the branch has no commits that its base does not already have",
    "no_gh": "the GitHub CLI (gh) is not installed",
    "gh_signed_out": "the GitHub CLI (gh) is not signed in; run `gh auth login`",
    "gh_unknown_host": (
        "the GitHub CLI (gh) is not signed in to the host in origin's URL (an SSH alias from "
        "~/.ssh/config is not a host gh knows; use the real host name in the URL)"
    ),
    "remote_unreachable": "origin could not be reached to see whether the branch already exists there",
    "bad_branch": "that is not a valid local branch name",
}

#: A credential inside a URL (``scheme://anything@``) and the shapes GitHub's own tokens take.
_URL_CREDENTIAL = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s]+@")
_GITHUB_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
#: ``host[:/]owner/repo[.git]`` out of an origin URL, for gh's ``--repo``.
_REPO_URL = re.compile(
    r"^(?:[a-z][a-z0-9+.-]*://)?(?:[^@/\s]+@)?(?P<host>[A-Za-z0-9.-]+)(?::\d+)?[:/]"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


def redact(text: str) -> str:
    """``text`` with any credential in a URL, and any GitHub token, replaced by ``***``."""
    return _GITHUB_TOKEN.sub("***", _URL_CREDENTIAL.sub(r"\1***@", text))


def sentence(reason: str) -> str:
    """The plain-English sentence for a reason word (the tool's observation; the screen translates)."""
    return _SENTENCES.get(reason, reason)


def _in_cwd(found: str) -> bool:
    """Whether a lookup result came from the current directory rather than from PATH."""
    if not os.path.isabs(found):
        return True
    try:
        return Path(found).resolve().parent == Path.cwd().resolve()
    except OSError:
        return True


def _on_path_only(name: str) -> str | None:
    """``name`` searched in PATH's absolute directories and nowhere else.

    :func:`shutil.which` on Windows puts the current directory before PATH (always on Python 3.11;
    on 3.12 unless ``NoDefaultCurrentDirectoryInExePath`` is set), and the chat and TUI run with the
    workspace as their current directory — so a ``gh.exe`` written into the workspace would be the
    one run, by `gh auth status`, before anyone is asked anything. A relative PATH entry (``.``) is
    the same door and is skipped too.
    """
    exts = [""]
    if sys.platform == "win32":
        listed = os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD"
        exts = [ext for ext in listed.split(os.pathsep) if ext]
    for entry in (os.environ.get("PATH") or "").split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        for ext in exts:
            candidate = os.path.join(entry, name + ext)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def gh_command() -> list[str] | None:
    """How to run gh here, or None when it is missing or would run through ``cmd.exe``.

    :func:`shutil.which` first (it is what every platform means by "installed"), and when its answer
    came from the current directory, the PATH-only search instead (:func:`_on_path_only`).
    """
    found = shutil.which("gh")
    if found and _in_cwd(found):
        found = _on_path_only("gh")
    if not found:
        return None
    if Path(found).suffix.lower() in {".bat", ".cmd"}:
        return None
    return [found]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        GIT_TERMINAL_PROMPT="0",
        GH_PROMPT_DISABLED="1",
        GH_NO_UPDATE_NOTIFIER="1",
        NO_COLOR="1",
        # gh reads a pager and an editor for some commands; neither may open here.
        GH_PAGER="",
        PAGER="",
    )
    return env


def run_quiet(
    argv: Sequence[str], cwd: Path, *, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """One process, no shell, UTF-8, bounded, no prompt and no pager. A failure to start is a failed
    result, never a raise. Public because the pull request watch (`scheduler/pr_watch.py`) runs gh
    the same way, and two runners are how one of them ends up with a shell or a prompt."""
    try:
        return subprocess.run(
            list(argv),
            cwd=cwd,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
            env=_env(),
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(list(argv), 127, "", f"could not run: {exc}")


#: Every git call here: an fsmonitor is a command `git status` would start, written in a config the
#: workspace holds.
_GIT = ("git", "-c", "core.fsmonitor=false")


def _git(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return run_quiet([*_GIT, *args], cwd)


def _valid_branch(name: str, cwd: Path) -> bool:
    if not name or name.startswith("-") or name == "HEAD":
        return False
    return _git(["check-ref-format", "--branch", name], cwd).returncode == 0


def _remote_default(root: Path, fetch_url: str) -> str:
    """origin's default branch: from ``origin/HEAD`` if the clone recorded it, else by asking.

    Asked of the URL, not of the name ``origin``: ``remote.origin.uploadpack`` is a command git runs
    on THIS machine when the URL is a local path."""
    local = _git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], root)
    if local.returncode == 0 and local.stdout.strip().startswith("origin/"):
        return local.stdout.strip()[len("origin/") :]
    asked = _git(["ls-remote", "--symref", "--", fetch_url, "HEAD"], root)
    for line in asked.stdout.splitlines():
        if line.startswith("ref: refs/heads/") and line.rstrip().endswith("HEAD"):
            return line[len("ref: refs/heads/") :].split("\t", 1)[0].strip()
    return ""


def _remote_branch(root: Path, push_url: str, name: str) -> str | None:
    """The commit ``refs/heads/<name>`` is at on the push URL; "" when it does not exist there,
    None when the remote could not be asked."""
    asked = _git(["ls-remote", "--heads", "--", push_url, f"refs/heads/{name}"], root)
    if asked.returncode != 0:
        return None
    for line in asked.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.strip() == f"refs/heads/{name}":
            return sha.strip()
    return ""


def repo_slug(remote_url: str) -> str:
    """``host/owner/name`` for gh's ``--repo``, or "" when the URL is not that shape (a local path)."""
    match = _REPO_URL.match(remote_url.strip())
    if not match:
        return ""
    return f"{match['host']}/{match['owner']}/{match['name']}"


@dataclass
class Readiness:
    """What opening a pull request from a workspace would do, or the first reason it cannot."""

    is_repo: bool = False
    branch: str = ""
    base: str = ""
    head: str = ""
    """The commit that would be pushed, in full. Sent back with the request, and pushed by hash."""
    remote: str = ""
    """Where the push goes: origin's PUSH URL, with any credential in it replaced by ``***``."""
    remote_head: str = ""
    """The commit the branch is at on origin now, or "" when the branch does not exist there. Not
    empty means the push UPDATES a branch other people may already read; the card says so."""
    ahead: int = 0
    commits: list[str] = field(default_factory=list)
    diffstat: str = ""
    uncommitted: int = 0
    """Changed files the push will NOT carry: the card says so, because a person reading "push this
    branch" assumes the edits on their screen go with it."""
    gh: bool = False
    gh_signed_in: bool = False
    reason: str = ""
    push_target: str = field(default="", repr=False)
    """The push URL as git has it, credential included: what the push is sent to. Never shown and
    never in :meth:`as_dict`."""

    @property
    def ready(self) -> bool:
        return not self.reason

    def as_dict(self) -> dict[str, Any]:
        out = {**asdict(self), "ready": self.ready}
        out.pop("push_target", None)
        return out


def _push_url(root: Path, fetch: str) -> tuple[str, bool]:
    """origin's push URL, and whether it is the repository the fetch URL names.

    ``get-url --push --all`` applies ``pushurl`` and ``pushInsteadOf``, which a plain ``get-url``
    does not: the plain one is the URL the card used to show while the push went elsewhere. The same
    repository written two ways (https to fetch, ssh to push) is the same repository."""
    listed = _git(["remote", "get-url", "--push", "--all", "origin"], root)
    urls = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    if listed.returncode != 0 or not urls:
        return fetch, True
    push = urls[0]
    if len(urls) > 1:
        return push, False
    if push == fetch:
        return push, True
    slug = repo_slug(push)
    return push, bool(slug) and slug == repo_slug(fetch)


def _gh_signed_in(command: Sequence[str], root: Path, push_url: str) -> str:
    """Empty when gh can open a pull request on the push URL's host, else the reason it cannot.

    Exit codes only: the text names the account and a masked token. With a host (``--hostname``),
    because a URL through an SSH alias (``git@github-work:o/r.git``) pushes fine and then leaves a
    branch published with no pull request, when gh does not know the alias as a host."""
    slug = repo_slug(push_url)
    if not slug:
        return "" if run_quiet([*command, "auth", "status"], root).returncode == 0 else "gh_signed_out"
    host = slug.split("/", 1)[0]
    if run_quiet([*command, "auth", "status", f"--hostname={host}"], root).returncode == 0:
        return ""
    if run_quiet([*command, "auth", "status"], root).returncode == 0:
        return "gh_unknown_host"
    return "gh_signed_out"


def readiness(
    ws: Path,
    *,
    branch: str | None = None,
    base: str | None = None,
    gh: Sequence[str] | None = None,
) -> Readiness:
    """Look before pushing: every fact the card shows, and the first reason it would be refused.

    ``branch`` defaults to the checked-out one; ``base`` to origin's default branch. ``gh`` is the
    command that runs the GitHub CLI (the tests pass a fake); None finds the installed one.
    """
    out = Readiness()
    root = Path(ws)
    if not is_git_repo(root):
        out.reason = "not_repo"
        return out
    out.is_repo = True
    current = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], root)
    name = (branch or "").strip() or (current.stdout.strip() if current.returncode == 0 else "")
    if not name:
        out.reason = "detached"
        return out
    if not _valid_branch(name, root) or _git(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], root
    ).returncode != 0:
        out.branch = name
        out.reason = "bad_branch"
        return out
    out.branch = name
    out.head = _git(["rev-parse", f"refs/heads/{name}"], root).stdout.strip()
    url = _git(["remote", "get-url", "origin"], root)
    if url.returncode != 0 or not url.stdout.strip():
        out.reason = "no_origin"
        return out
    fetch = url.stdout.strip()
    push, same_repo = _push_url(root, fetch)
    out.remote = redact(push)
    out.push_target = push
    if not same_repo:
        out.reason = "push_elsewhere"
        return out
    default = _remote_default(root, fetch)
    wanted = (base or "").strip() or default
    if not wanted or not _valid_branch(wanted, root):
        out.reason = "no_base"
        return out
    out.base = wanted
    if name in (wanted, default) or name in _USUAL_DEFAULTS:
        out.reason = "default_branch"
        return out
    base_ref = next(
        (
            ref
            for ref in (f"refs/remotes/origin/{wanted}", f"refs/heads/{wanted}")
            if _git(["rev-parse", "--verify", "--quiet", ref], root).returncode == 0
        ),
        "",
    )
    if not base_ref:
        out.reason = "no_base"
        return out
    span = f"{base_ref}..refs/heads/{name}"
    count = _git(["rev-list", "--count", span], root)
    out.ahead = int(count.stdout.strip()) if count.stdout.strip().isdigit() else 0
    log = _git(["log", f"-n{_MAX_COMMITS}", "--format=%h %s", span], root)
    out.commits = [line for line in log.stdout.splitlines() if line.strip()]
    # No external diff and no textconv: both are commands named in a config the workspace holds.
    stat = _git(
        ["diff", "--no-ext-diff", "--no-textconv", "--stat", f"{base_ref}...refs/heads/{name}"], root
    )
    out.diffstat = stat.stdout.strip()[:_MAX_OUTPUT]
    status = _git(["status", "--porcelain=v1"], root)
    out.uncommitted = sum(1 for line in status.stdout.splitlines() if line.strip())
    if out.ahead == 0:
        out.reason = "nothing_ahead"
        return out
    command = list(gh) if gh is not None else gh_command()
    out.gh = bool(command)
    if not command:
        out.reason = "no_gh"
        return out
    signed_out = _gh_signed_in(command, root, push)
    out.gh_signed_in = not signed_out
    if signed_out:
        out.reason = signed_out
        return out
    at = _remote_branch(root, push, name)
    if at is None:
        out.reason = "remote_unreachable"
        return out
    out.remote_head = at
    return out


def _failed(error: str, output: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "url": "",
        "output": redact(output)[:_MAX_OUTPUT],
        "error": redact(error)[:_MAX_ERR],
    }


def check_text(title: str, body: str) -> str:
    """Why this title and body cannot be sent, or "" when they can."""
    if not title.strip():
        return "the title is empty"
    if "\n" in title or "\r" in title:
        return "the title must be one line"
    if len(title.strip()) > MAX_TITLE:
        return f"the title is longer than {MAX_TITLE} characters"
    if len(body) > MAX_BODY:
        return f"the description is longer than {MAX_BODY} characters"
    return ""


def open_pull_request(
    ws: Path,
    *,
    title: str,
    body: str,
    expect_head: str,
    branch: str | None = None,
    base: str | None = None,
    draft: bool = False,
    gh: Sequence[str] | None = None,
    expect_remote: str | None = None,
    expect_remote_head: str | None = None,
) -> dict[str, Any]:
    """Push the reviewed commit to ``origin/<branch>`` and open the pull request. ``{ok, url, output, error}``.

    Every check :func:`readiness` makes is made again here, because whatever showed them may be
    minutes old. ``expect_head`` is required: it is what makes the answer an answer about THIS
    commit, and a call without it has nothing to compare the branch against. ``expect_remote`` and
    ``expect_remote_head`` are the destination and the remote branch's commit that were SHOWN
    (``Readiness.remote`` / ``remote_head``); given, a destination or a remote branch that changed
    since is refused. The agent's tool always gives them; the Git panel sends what it showed.
    """
    problem = check_text(title, body)
    if problem:
        return _failed(problem)
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", expect_head or ""):
        return _failed("the reviewed commit is missing; look at the branch again before opening")
    state = readiness(ws, branch=branch, base=base, gh=gh)
    if not state.ready:
        return _failed(sentence(state.reason))
    if state.head != expect_head:
        return _failed(
            "the branch moved after it was reviewed; look at it again before opening the pull request"
        )
    if expect_remote is not None and state.remote != expect_remote:
        return _failed(
            "origin's push URL changed after it was reviewed; look at it again before opening"
        )
    if expect_remote_head is not None and state.remote_head != expect_remote_head:
        return _failed(
            f"the branch {state.branch} on origin changed after it was reviewed; look at it again "
            "before opening"
        )
    root = Path(ws)
    command = list(gh) if gh is not None else (gh_command() or [])
    # By hash, to an explicit ref, with nothing that could force it; to the URL that was shown, not
    # to the name `origin` (whose pushurl is read again at push time, and whose `receivepack` is a
    # command); and with no hook: `--no-verify` skips pre-push, an empty hooksPath everything else.
    with tempfile.TemporaryDirectory(prefix="chimera-no-hooks-") as no_hooks:
        push = run_quiet(
            [
                *_GIT,
                "-c",
                f"core.hooksPath={no_hooks}",
                "push",
                "--no-verify",
                "--porcelain",
                "--",
                state.push_target,
                f"{state.head}:refs/heads/{state.branch}",
            ],
            root,
        )
    pushed = (push.stdout + push.stderr).strip()
    if push.returncode != 0:
        return _failed(push.stderr.strip() or "git push failed", pushed)
    slug = repo_slug(state.push_target)
    create = run_quiet(
        [
            *command,
            "pr",
            "create",
            *([f"--repo={slug}"] if slug else []),
            f"--head={state.branch}",
            f"--base={state.base}",
            f"--title={title.strip()}",
            "--body-file=-",
            *(["--draft"] if draft else []),
        ],
        root,
        stdin=body,
    )
    output = "\n".join(part for part in (pushed, create.stdout.strip(), create.stderr.strip()) if part)
    if create.returncode != 0:
        # The branch is on origin now and stays there: deleting a remote branch is not something a
        # failed `gh` call gets to do on the owner's behalf. The error says so.
        return _failed(
            (create.stderr.strip() or "gh pr create failed")
            + f" (the branch {state.branch} was pushed to origin and is still there)",
            output,
        )
    links = [word for word in create.stdout.split() if word.startswith(("https://", "http://"))]
    return {
        "ok": True,
        "url": redact(links[-1]) if links else "",
        "output": redact(output)[:_MAX_OUTPUT],
        "error": None,
    }
