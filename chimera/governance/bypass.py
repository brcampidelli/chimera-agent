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

Every check that decides (b) and (c) also reads the commands nested in ``bash -c``, ``cmd /c`` and
``powershell -Command``, because a shell wrapper changes nothing about what runs.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

_SHELL_TOOLS = frozenset({"run_shell", "execute_code", "code_interpreter"})
_PATH_LIKE_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
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


def bypass_safe(tool: str, args: dict[str, Any], kernel_evaluation: Any = None) -> bool:
    """Whether a permission bypass may release this action, including its kernel REVIEW.

    False only for the three owner-kept cases in the module docstring. Everything else returns True,
    so a call that is merely REVIEW-worthy runs without a card once the switch is on.
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
    if tool in _SHELL_TOOLS and (
        _has_secret_disclosure(text)
        or _encoded_powershell(text)
        or _rewrites_default_branch(text)
    ):
        return False

    verdict = RuleSet().evaluate(action)
    return verdict is None or verdict.decision != Decision.BLOCK


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
    force = delete = mass = all_branches = False
    positional: list[str] = []
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
            continue
        if arg in _PUSH_VALUE_FLAGS:
            skip_value = True
            continue
        if arg.startswith("--"):
            flag = arg.split("=", 1)[0].casefold()
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
    # git's own reading: the first positional is the remote, the rest are refspecs. With no refspec
    # the pushed branch is the current one, which the text cannot name, so a force there is asked.
    refspecs = positional[1:]
    if not refspecs:
        return force
    for refspec in refspecs:
        is_delete = delete or refspec.startswith(":")
        destination = refspec[1:] if refspec.startswith(":") else refspec.rsplit(":", 1)[-1]
        forced = force or refspec.startswith("+")
        destination = _ref_name(destination.lstrip("+"))
        if destination in _DEFAULT_BRANCHES and (forced or is_delete):
            return True
        if forced and (destination in {"head", "@"} or "*" in destination):
            return True
    return False


def _ref_name(ref: str) -> str:
    lowered = ref.casefold()
    return lowered[len("refs/heads/"):] if lowered.startswith("refs/heads/") else lowered


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


def _expanded_token_sets(command: str, depth: int = 0) -> list[list[str]]:
    """The command's token sets, plus every script a shell wrapper runs, recursively."""
    sets: list[list[str]] = []
    for tokens in _command_token_sets(command):
        sets.append(tokens)
        if depth >= _MAX_NESTING:
            continue
        for index in range(len(tokens)):
            nested = _nested_script(tokens, index)
            if nested:
                sets.extend(_expanded_token_sets(nested, depth + 1))
    return sets


def _nested_script(tokens: list[str], index: int) -> str:
    """The script a shell wrapper at ``tokens[index]`` runs, or '' when the token is not a wrapper."""
    executable = _basename(tokens[index])
    following = tokens[index + 1:]
    if executable in _PATH_LIKE_SHELLS and following and following[0].startswith("-") and "c" in following[0][1:]:
        return " ".join(tokens[index + 2:])
    if executable == "cmd" and following and following[0].casefold() in {"/c", "/k"}:
        return " ".join(tokens[index + 2:])
    if executable in {"powershell", "pwsh"}:
        for position, flag in enumerate(following):
            if flag.casefold() in {"-command", "-c"}:
                return " ".join(tokens[index + 2 + position:])
    return ""


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
