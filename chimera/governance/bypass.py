"""Shared safety floor for the owner's convenience permission switch.

Owner decision (26/10): with the per-conversation "Ignore permissions" switch ON, the agent acts
directly on every connection it has: git and pushes to GitHub, ``gh``, MCP connector tools, deploys,
uploads, HTTP writes and send tools, in a clean run and in a tainted one. Three things still come back
to the owner, and nothing else does:

(a) the kernel's BLOCK rules and the destructive denylist (``rm -rf /``, ``mkfs``, ...);
(b) sending or reading a secret file (``is_secret_file``) as part of an outbound command, and a
    PowerShell ``-EncodedCommand``;
(c) rewriting the default branch's history on a remote: a force push, a ``+refspec``, or a delete
    of ``main``/``master``. A force push to a feature branch is released.

And one more, added after the audit of 2026-11 (a07-04) found that on Windows, with no OS sandbox,
nothing asked before a recursive delete of ``C:/Users``:

(d) a shell or code command that deletes or writes RECURSIVELY outside the workspace and outside the
    home folder (:mod:`chimera.governance.bypass_reach`). It is a lexical rule and it says so: it
    REDUCES what the switch releases, it does not close it. A variable, a script file or a path
    built at run time is not seen.

Every check that decides (b) and (c) also reads the commands nested in ``bash -c``, ``cmd /c`` and
``powershell -Command``, because a shell wrapper changes nothing about what runs.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from chimera.governance.bypass_reach import MAX_COMMAND_CHARS, is_wrapper_word, nested_script

_SHELL_TOOLS = frozenset({"run_shell", "execute_code", "code_interpreter"})
_DEFAULT_BRANCHES = frozenset({"main", "master"})
_SECRET_TERMS = ("secret", "credential", "exfiltrat")
#: Shell readers: reading a secret with one of these, then naming it, is a disclosure in the making.
_READERS = frozenset({
    "cat", "type", "get-content", "gc", "more", "less", "head", "tail", "cp", "copy", "scp", "rsync",
    "xxd", "base64", "strings",
})
#: Executables that talk to something outside this machine. A secret named in the same command as
#: one of these is being sent, whatever the flags say.
_OUTBOUND = frozenset({
    "curl", "wget", "gh", "scp", "rsync", "sftp", "ftp", "nc", "ncat", "netcat", "rclone", "vercel",
    "netlify", "aws", "gcloud", "az", "docker", "npm", "pnpm", "yarn", "twine", "http", "httpie",
    "kubectl", "terraform", "stripe", "glab", "hub",
})
#: `git push` options that take a value; the value is not a remote or a refspec.
_PUSH_VALUE_FLAGS = frozenset({"--repo", "--receive-pack", "--exec", "-o", "--push-option"})
_MAX_NESTING = 4


def bypass_safe(
    tool: str,
    args: dict[str, Any],
    kernel_evaluation: Any = None,
    *,
    workspace: Path | str | None = None,
    home: Path | str | None = None,
) -> bool:
    """Whether a permission bypass may release this action, including its kernel REVIEW.

    Fails closed: if reading the call raises for ANY reason, the answer is no and the owner is asked.
    The checks themselves are in :func:`_bypass_safe`.
    """
    try:
        return _bypass_safe(tool, args, kernel_evaluation, workspace=workspace, home=home)
    except Exception:  # a call this floor cannot read stays with the owner
        return False


def _bypass_safe(
    tool: str,
    args: dict[str, Any],
    kernel_evaluation: Any = None,
    *,
    workspace: Path | str | None = None,
    home: Path | str | None = None,
) -> bool:
    """Whether a permission bypass may release this action, including its kernel REVIEW.

    False only for the owner-kept cases in the module docstring. Everything else returns True, so a
    call that is merely REVIEW-worthy runs without a card once the switch is on.

    ``workspace`` and ``home`` place the targets of (d); a caller that does not know the workspace
    passes nothing, and then an absolute target is judged against the home alone. ``home`` defaults
    to this user's home folder.
    """
    from chimera.governance.ledger import describe_call
    from chimera.governance.policy import Decision, RuleSet

    if getattr(kernel_evaluation, "decision", None) == Decision.BLOCK:
        return False
    evaluation = " ".join(
        str(getattr(kernel_evaluation, key, "")) for key in ("reason", "rule", "category")
    ).casefold()
    if any(term in evaluation for term in _SECRET_TERMS):
        return False
    if _names_secret_file(args):
        return False

    action = describe_call(tool, args)
    command = " ".join(str(args[key]) for key in ("command", "cmd", "code") if key in args)
    text = command or action
    if tool in _SHELL_TOOLS and len(text) > MAX_COMMAND_CHARS:
        return False  # too long to read in a useful time: the owner is asked, nothing is analysed
    if tool in _SHELL_TOOLS and any(check(text) for _category, check in _SHELL_FLOOR):
        return False
    if tool in _SHELL_TOOLS:
        from chimera.governance.bypass_reach import reaches_outside

        cwd = args.get("cwd") if isinstance(args.get("cwd"), str) else None
        if reaches_outside(text, workspace=workspace, home=home, cwd=cwd):
            return False

    verdict = RuleSet().evaluate(action)
    return verdict is None or verdict.decision != Decision.BLOCK


def floor_categories() -> list[str]:
    """What still comes back to the owner with the switch on, by name, in the order it is checked.

    Read off the same checks :func:`bypass_safe` runs (the shell ones from :data:`_SHELL_FLOOR`), so
    the security card cannot list a category the floor does not apply, or miss one it does. No
    setting turns any of them off; the list is the same on every machine.
    """
    return [
        "kernel_block",
        "secret_file",
        *(category for category, _check in _SHELL_FLOOR),
        "outside_reach",
    ]


def _names_secret_file(args: dict[str, Any]) -> bool:
    """A secret file passed as a whole argument value: ``read_file {"path": ".env"}``, an upload."""
    from chimera.api.bridge_routes import is_secret_file

    return any(
        isinstance(value, str) and "\n" not in value and is_secret_file(value.lstrip("@"))
        for value in args.values()
    )


def _has_secret_disclosure(command: str) -> bool:
    """(b): a secret file read locally, or named in the same command as an outbound executable."""
    for tokens in _expanded_token_sets(command):
        secret_indexes = [index for index, token in enumerate(tokens) if _is_secret_token(token)]
        if not secret_indexes:
            continue
        for index, token in enumerate(tokens):
            executable = _basename(token)
            if any(secret > index for secret in secret_indexes) and (
                executable in _READERS or executable in {"powershell", "pwsh"}
            ):
                return True
            if _is_outbound(tokens, index):
                return True
    return False


def _is_secret_token(token: str) -> bool:
    """A token that names a secret file, bare or as an upload value: ``.env``, ``@.env``, ``f=@.env``."""
    from chimera.api.bridge_routes import is_secret_file

    return any(is_secret_file(piece) for piece in re.split(r"[=@]", token) if piece)


def _is_outbound(tokens: list[str], index: int) -> bool:
    executable = _basename(tokens[index])
    if executable == "git":
        return _git_subcommand(tokens, index + 1) in {"push", "send-email"}
    return executable in _OUTBOUND


def _encoded_powershell(command: str) -> bool:
    """(b): ``-EncodedCommand`` in any spelling PowerShell accepts (``-e``, ``-enc``, ...)."""
    return any(
        _basename(token) in {"powershell", "pwsh"}
        and any(_is_encoded_flag(arg) for arg in tokens[index + 1:])
        for tokens in _expanded_token_sets(command)
        for index, token in enumerate(tokens)
    )


def _is_encoded_flag(arg: str) -> bool:
    lowered = arg.casefold()
    return len(lowered) >= 2 and lowered[0] == "-" and "encodedcommand".startswith(lowered[1:])


def _rewrites_default_branch(command: str) -> bool:
    """(c): a git push that rewrites or deletes main/master on a remote, or the GitHub API doing so."""
    for tokens in _expanded_token_sets(command):
        for index, token in enumerate(tokens):
            executable = _basename(token)
            if executable == "git":
                subcommand = _git_subcommand_index(tokens, index + 1)
                if (
                    subcommand is not None
                    and tokens[subcommand].casefold() == "push"
                    and _push_rewrites_default_branch(tokens[subcommand + 1:])
                ):
                    return True
            if executable == "gh" and _gh_api_rewrites_default_ref(tokens, index):
                return True
    return False


def _push_rewrites_default_branch(args: list[str]) -> bool:
    """Decide from the push's own arguments; the flags may come before, between or after the refspecs."""
    force = delete = mass = all_branches = repo_flag = False
    positional: list[str] = []
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
            continue
        if arg in _PUSH_VALUE_FLAGS:
            skip_value = True
            repo_flag = repo_flag or arg == "--repo"
            continue
        if arg.startswith("--"):
            flag = arg.split("=", 1)[0].casefold()
            repo_flag = repo_flag or flag == "--repo"
            if flag.startswith("--force"):  # --force, --force-with-lease, --force-if-includes
                force = True
            elif flag == "--delete":
                delete = True
            elif flag in {"--mirror", "--prune"}:
                mass = True
            elif flag == "--all":
                all_branches = True
            continue
        if arg.startswith("-") and len(arg) > 1:
            force = force or "f" in arg[1:]
            delete = delete or "d" in arg[1:]
            continue
        positional.append(arg)

    if mass or (all_branches and force):
        return True
    # git's own reading: the first positional is the remote, the rest are refspecs. When the remote came
    # by ``--repo`` (a flag, with or without ``=``) every positional is a refspec instead, and reading
    # only the old way let ``--repo=origin -f HEAD:main HEAD:feature`` through. Both readings are
    # taken and either one asks: the new reading adds refusals, it never takes one away (the old one
    # asked a force with no refspec, and ``--repo=origin -f HEAD:feature`` has none by that count).
    readings = [positional[1:]]
    if repo_flag:
        readings.append(positional)
    return any(_refspecs_rewrite(refspecs, force=force, delete=delete) for refspecs in readings)


def _refspecs_rewrite(refspecs: list[str], *, force: bool, delete: bool) -> bool:
    # With no refspec the pushed branch is the current one, which the text cannot name, so a force
    # there is asked.
    if not refspecs:
        return force
    for refspec in refspecs:
        is_delete = delete or refspec.startswith(":")
        destination = refspec[1:] if refspec.startswith(":") else refspec.rsplit(":", 1)[-1]
        forced = force or refspec.startswith("+")
        destination = _ref_name(destination.lstrip("+"))
        if destination == "" and forced:
            return True  # `+:` / `:` with a force pushes every matching branch, main among them
        if destination in _DEFAULT_BRANCHES and (forced or is_delete):
            return True
        if forced and (destination in {"head", "@"} or "*" in destination):
            return True
    return False


def _ref_name(ref: str) -> str:
    lowered = ref.casefold()
    return lowered[len("refs/heads/"):] if lowered.startswith("refs/heads/") else lowered


#: The floor's checks on the text of a shell or code command, each with the category the security card
#: names it by. Run in this order by :func:`bypass_safe`; any one of them keeps the call with the owner.
_SHELL_FLOOR: tuple[tuple[str, Callable[[str], bool]], ...] = (
    ("secret_disclosure", _has_secret_disclosure),
    ("encoded_powershell", _encoded_powershell),
    ("default_branch_rewrite", _rewrites_default_branch),
)


def _gh_api_rewrites_default_ref(tokens: list[str], index: int) -> bool:
    """``gh api -X DELETE repos/o/r/git/refs/heads/main`` removes the default branch as surely as a push."""
    if _next(tokens, index) != "api":
        return False
    rest = tokens[index + 2:]
    method = "GET"
    for position, arg in enumerate(rest):
        if arg in {"-X", "--method"} and position + 1 < len(rest):
            method = rest[position + 1].upper()
        elif arg.startswith("--method="):
            method = arg.split("=", 1)[1].upper()
        elif arg in {"-f", "-F", "--field", "--raw-field", "--input"} and method == "GET":
            method = "POST"
    if method == "GET":
        return False
    return any(re.search(r"refs/heads/(main|master)(?![\w/.-])", arg, re.IGNORECASE) for arg in rest)


class _TooDeepToRead(Exception):
    """A command that wraps scripts in scripts past the cap: :func:`bypass_safe` answers no."""


#: How many scripts one command may be unwrapped into before it is given up on.
_MAX_EXPANSIONS = 64


def _expanded_token_sets(command: str, depth: int = 0, budget: list[int] | None = None) -> list[list[str]]:
    """The command's token sets, plus every script a shell wrapper runs, recursively.

    A script found past ``_MAX_NESTING``, or more scripts than ``_MAX_EXPANSIONS``, is not read: it
    raises, and the floor's answer to a call it cannot read is no (``eval eval eval ... rm -rf X`` was
    released because the reading stopped quietly at the cap, and ``eval`` x40 took 18 s).
    """
    remaining = [_MAX_EXPANSIONS] if budget is None else budget
    sets: list[list[str]] = []
    for tokens in _command_token_sets(command):
        sets.append(tokens)
        for index in range(len(tokens)):
            if index and is_wrapper_word(tokens[index - 1]):
                continue  # the wrapper before it already unwrapped this one (and a long chain stays linear)
            nested = _nested_script(tokens, index)
            if not nested:
                continue
            remaining[0] -= 1
            if depth >= _MAX_NESTING or remaining[0] < 0:
                raise _TooDeepToRead
            sets.extend(_expanded_token_sets(nested, depth + 1, remaining))
    return sets


def _nested_script(tokens: list[str], index: int) -> str:
    """The script a shell wrapper at ``tokens[index]`` runs, or '' when the token is not a wrapper.

    The reading itself lives in :func:`chimera.governance.bypass_reach.nested_script`, one copy for the
    whole floor: a second one here read only the first argument after ``bash`` and missed
    ``bash --noprofile -c``, ``bash -l -c``, ``powershell -Comm`` and ``eval``.
    """
    return nested_script(tokens[index:])


def wraps_approver(
    approver: Any,
    *,
    tool: str,
    args: dict[str, Any],
    kernel_evaluation: Any = None,
) -> Any:
    """Return yes for a safe bypass; otherwise preserve the configured approver unchanged."""
    def approve(assessment: Any, action: str | None = None) -> bool:
        if bypass_safe(tool, args, kernel_evaluation):
            return True
        if approver is None:
            return False
        try:
            return bool(approver(assessment, action))
        except TypeError:
            return bool(approver(assessment))

    return approve


def _command_token_sets(command: str) -> list[list[str]]:
    """Tokenize shell/PowerShell spellings, normalizing executable and grouping syntax."""
    normalized = re.sub(r"[(){}\[\],$`]+", " ", command)
    parsed: list[list[str]] = []
    for posix in (True, False):
        try:
            tokens = [token.strip("\\\"'") for token in shlex.split(normalized, posix=posix) if token.strip("\\\"'")]
        except ValueError:
            continue
        if tokens and tokens not in parsed:
            parsed.append(tokens)
    return parsed


def _basename(token: str) -> str:
    return re.split(r"[/\\]", token.casefold())[-1].removesuffix(".exe")


def _next(tokens: list[str], index: int) -> str:
    return tokens[index + 1].casefold() if index + 1 < len(tokens) else ""


def _git_subcommand_index(tokens: list[str], index: int) -> int | None:
    """Where git's subcommand sits, skipping global options (``-C path``, ``-c key=value``, ...)."""
    while index < len(tokens):
        arg = tokens[index]
        if arg in {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}:
            index += 2
            continue
        if arg.startswith(("--git-dir=", "--work-tree=", "--namespace=")) or arg.startswith("-"):
            index += 1
            continue
        return index
    return None


def _git_subcommand(tokens: list[str], index: int) -> str:
    subcommand = _git_subcommand_index(tokens, index)
    return tokens[subcommand].casefold() if subcommand is not None else ""
