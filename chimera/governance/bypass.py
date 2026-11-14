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

What this floor does NOT guarantee. It reads shell text. A command it cannot read with confidence is
held for the owner (fail-closed), but a command it believes it read can still mean something else to
the shell, and some routes never pass through shell text at all:

- code run by an interpreter or an arbitrary program (an inline Python or Node snippet, a TLS client,
  a raw socket) that opens a credential and sends it;
- a script the agent writes in one call and runs in the next;
- a file tool reading a credential file inside the workspace (held only by the switch, pending an
  owner decision on whether the kernel should ask for every ``.env`` edit);
- an MCP connector tool that runs commands on the other side.

Those belong to taint, the sandbox and the approval of artifacts, not to a longer word list. Review
rounds of 2026-11-14 also listed narrower gaps in the reading itself (redirections glued to a
refspec, a command name built at run time, assignments before ``git``, globs, git configuration
through the environment, the effective ``cd``/``cwd``, and symlink resolution limits); they are
tracked as follow-up work, not claimed as closed here.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from chimera.governance.bypass_reach import (
    MAX_COMMAND_CHARS,
    is_wrapper_word,
    may_wrap,
    nested_script,
)

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
    # PowerShell's own (second review of the P0-01 fix: `Invoke-WebRequest -InFile .env` was released).
    "invoke-webrequest", "invoke-restmethod", "iwr", "irm", "start-bitstransfer", "send-mailmessage",
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
    if _names_secret_file(args, workspace):
        return False

    action = describe_call(tool, args)
    command = " ".join(str(args[key]) for key in ("command", "cmd", "code") if key in args)
    text = command or action
    # Fail-closed: too long, unreadable, built at run time, or one of the named categories.
    if tool in _SHELL_TOOLS and shell_floor(text, workspace=workspace) is not None:
        return False
    if tool in _SHELL_TOOLS:
        from chimera.governance.bypass_reach import reaches_outside

        cwd = args.get("cwd") if isinstance(args.get("cwd"), str) else None
        if reaches_outside(text, workspace=workspace, home=home, cwd=cwd):
            return False

    verdict = RuleSet().evaluate(action)
    return verdict is None or verdict.decision != Decision.BLOCK


#: Tools whose CARD the switch never answers, however the call judges: a question put to the person
#: (`ask_user`), a pull request (`always_ask`), the browser's own navigation cards. Card path only
#: (``question`` given to :func:`release_by_bypass`): at the tool boundary a tainted browser step is
#: released like any other connection, by the owner's decision of 26/10.
NEVER_RELEASED_TOOLS = frozenset({"ask_user", "open_pull_request", "browser"})
#: Question levels the switch never answers.
_NEVER_RELEASED_LEVELS = frozenset({"block", "warn"})


def release_by_bypass(
    tool: str,
    args: dict[str, Any],
    verdict: Any = None,
    *,
    shown: str = "",
    workspace: Path | str | None = None,
    home: Path | str | None = None,
    question: Any = None,
    rules_home: Path | str | None = None,
) -> bool:
    """THE rule: whether the owner's "Ignore permissions" switch may answer this call yes.

    Every place the switch releases something calls this, and nothing else decides it: the kernel's
    REVIEW at the tool boundary (`governed_tool`), the taint ledger (`ledger_tool`), the owner's
    approver behind the card (`code_api.bypass_owner_for`) and a card already waiting when the switch
    is turned on (`waiting_release`). Until the audit of 2026-11 (a07-01, a07-07) the card's approver
    had a copy of its own that never called :func:`bypass_safe`, and it was the approver the boundary
    falls back to when the floor says no — so a force push to ``main``, ``git push origin +main``,
    ``curl -T .env`` and ``powershell -EncodedCommand`` were refused by the floor and then answered
    yes one step later, without a card.

    Every check here can only refuse; any one of them sends the call to the owner:

    - the floor (:func:`bypass_safe`) on the call's own arguments, with the verdict;
    - the floor on the text the card shows: ``shown``, the action as the boundary rendered it (without
      the block of resolved programs `exec_facts.annotate` appends), or, when the question came with
      no call behind it, the question's own text, read whole;
    - the policy's BLOCK or WARN on that text;
    - on the card path (``question`` given): the question's level is BLOCK or WARN, the tool is in
      :data:`NEVER_RELEASED_TOOLS`, or the owner saved an approval rule for it (``rules_home`` and
      ``workspace`` given; the rule is the owner's own answer and the switch must not override it).

    Fails closed: anything that raises is a no.
    """
    try:
        return _release_by_bypass(
            tool, args, verdict, shown=shown, workspace=workspace, home=home, question=question,
            rules_home=rules_home,
        )
    except Exception:  # a call this rule cannot read stays with the owner
        return False


def _release_by_bypass(
    tool: str,
    args: dict[str, Any],
    verdict: Any,
    *,
    shown: str,
    workspace: Path | str | None,
    home: Path | str | None,
    question: Any,
    rules_home: Path | str | None,
) -> bool:
    from chimera.governance.policy import Decision, RuleSet

    # The card's text: what the boundary showed when it named the call, else the question's own. Never
    # both, because the question carries the same text the boundary showed.
    texts = [_command_text(shown)] if shown else [text for text in (_question_text(question),) if text]
    if _level(verdict) in _NEVER_RELEASED_LEVELS:
        return False
    if question is not None and not _card_releasable(tool, question, texts, workspace, rules_home):
        return False
    if not bypass_safe(tool, args, verdict, workspace=workspace, home=home):
        return False
    for text in dict.fromkeys(texts):
        if not bypass_safe(tool, {"command": text}, workspace=workspace, home=home):
            return False
        policy = RuleSet().evaluate(text)
        if policy is not None and policy.decision in {Decision.BLOCK, Decision.WARN}:
            return False
    return True


def _card_releasable(
    tool: str,
    question: Any,
    texts: list[str],
    workspace: Path | str | None,
    rules_home: Path | str | None,
) -> bool:
    """The checks only a card has: its level, its tool, and the owner's saved rule for it."""
    from chimera.governance.approval_rules import matching_rule, tool_and_action
    from chimera.governance.policy import Decision, RuleSet

    if _level(question) in _NEVER_RELEASED_LEVELS:
        return False
    if tool in NEVER_RELEASED_TOOLS:
        return False
    for text in texts:
        card_tool, card_shown = tool_and_action(text)
        if card_tool in NEVER_RELEASED_TOOLS:
            return False
        # The card's first line, `tool: target`, is the shape the approval rules and the policy were
        # matched on before the rule was shared: read that way too, so nothing that asked still asks less.
        policy = RuleSet().evaluate(card_shown)
        if policy is not None and policy.decision in {Decision.BLOCK, Decision.WARN}:
            return False
        if (
            rules_home is not None
            and workspace is not None
            and matching_rule(Path(rules_home), Path(workspace), card_tool, card_shown) is not None
        ):
            return False
    return True


def _level(evaluation: Any) -> str:
    """A verdict's or a question's level as a lower-case word ('' when it has none)."""
    decision = getattr(evaluation, "decision", None)
    return str(getattr(decision, "value", decision) or "").strip().lower()


def _command_text(shown: str) -> str:
    """``shown`` without the block of facts the boundary appended (`exec_facts.annotate`).

    The block names the programs a command resolves to and the git hooks it sets off; it is the
    machine's prose, not the command, and the floor's lexer read it as more arguments of the command:
    ``git push --force origin feat/x`` followed by ``- git push: no repository hook will run (...)``
    read as a push with more refspecs, and a force push to a feature branch, released by rule (c), was
    asked through `profile`'s closure. Cut at the header only, which `annotate` defuses in the text
    the model wrote, so a command cannot carry a copy of it to hide its own tail.
    """
    from chimera.governance.exec_facts import HEADER

    return shown.split("\n\n" + HEADER, 1)[0]


def _question_text(question: Any) -> str:
    return str(getattr(question, "action", "") or "") if question is not None else ""


def card_call(action: str) -> tuple[str, dict[str, Any]]:
    """The tool and arguments a card's text stands for, when the question came without its call.

    A card asked outside :func:`chimera.governance.pending.asking_for_call` (the host-command
    confirm, a file tool's question) carries only its text: ``run_shell: <command>``, or the ledger's
    ``run_shell\\n  command: <command>``. The command is everything after the tool's name, so the
    floor reads the whole of it; the old copy read only the first line, and on the ledger's shape
    that was the bare word ``run_shell``.
    """
    head, _, rest = action.partition("\n")
    tool, separator, target = head.partition(":")
    command = (target + ("\n" + rest if rest else "")) if separator else rest
    return tool.strip(), {"command": command.strip()}


#: The sentence a REVIEW raised by the floor carries, by category.
_FLOOR_REASONS = {
    "unreadable": "a command the owner's floor cannot read with confidence",
    "secret_disclosure": "reads a credential file or sends it out",
    "encoded_powershell": "PowerShell -EncodedCommand: the command it runs cannot be read",
    "default_branch_rewrite": "rewrites or deletes main/master on a remote, or may",
}


def floor_review_reason(
    tool: str, args: dict[str, Any], *, workspace: Path | str | None = None
) -> str | None:
    """Why the kernel must ask about this call even when its rules allowed it, or ``None``.

    The floor's shell cases that do not depend on where a command reaches (:func:`shell_floor`): a
    command it cannot read, (b) a credential file read or sent out or an encoded PowerShell command,
    and (c) a rewrite of the default branch. `GovernedTool` raises an ALLOW or WARN to REVIEW on
    them, so the floor holds where no kernel rule asked. The recursive write outside the workspace
    (d) is left to the switch: it judges where a command reaches, not what it is.

    Shell and code commands only. A file tool's credential path is already the switch's to keep
    (`_names_secret_file` in `bypass_safe`), and lifting it in the kernel for every mode would ask
    the owner about every `.env` edit in a project, a policy change nobody decided
    (`test_action_rendering` pins a `.env` write as WARN, `bench/governance_axes` the probes the
    kernel leaves open).
    """
    if tool not in _SHELL_TOOLS:
        return None
    text = " ".join(str(args[key]) for key in ("command", "cmd", "code") if key in args)
    if not text.strip():
        return None
    category = shell_floor(text, workspace=workspace)
    return _FLOOR_REASONS[category] if category is not None else None


def shell_floor(command: str, *, workspace: Path | str | None = None) -> str | None:
    """The floor category that keeps this shell or code command for the owner, or ``None``.

    FAIL-CLOSED (second review of the P0-01 fix). A lexical floor that lists dangerous spellings is
    never complete: eight more were found after the first six were fixed, and most shared one cause,
    a reading that failed or could not see the word was read as "nothing dangerous here". So a
    command the floor cannot read with confidence is ``unreadable`` and goes to the owner:

    - longer than :data:`MAX_COMMAND_CHARS`;
    - no tokenisation succeeds (an unterminated quote, including one inside a ``#`` comment);
    - wraps scripts in scripts past the cap;
    - builds words at run time (``$(``, a backtick, ``$'...'``, ``${``, a line continuation) in a
      command the floor reads for (b) or (c): a push, an outbound command, PowerShell.

    Then the named categories, in the order :func:`floor_categories` lists them.
    """
    if len(command) > MAX_COMMAND_CHARS:
        return "unreadable"
    try:
        sets = _expanded_token_sets(command)
        if _builds_words_at_run_time(command, sets):
            return "unreadable"
        for category, check in _SHELL_FLOOR:
            if check(sets, workspace):
                return category
    except _Unreadable:
        return "unreadable"
    return None


def floor_categories() -> list[str]:
    """What still comes back to the owner with the switch on, by name, in the order it is checked.

    Read off the same checks :func:`bypass_safe` runs (the shell ones from :func:`shell_floor`), so
    the security card cannot list a category the floor does not apply, or miss one it does. No
    setting turns any of them off; the list is the same on every machine.
    """
    return [
        "kernel_block",
        "secret_file",
        "unreadable",
        *(category for category, _check in _SHELL_FLOOR),
        "outside_reach",
    ]


def _names_secret_file(args: dict[str, Any], workspace: Path | str | None = None) -> bool:
    """A secret file passed as a whole argument value: ``read_file {"path": ".env"}``, an upload."""
    from chimera.api.bridge_routes import is_secret_file

    budget = [_MAX_RESOLVED]
    return any(
        isinstance(value, str)
        and "\n" not in value
        and (is_secret_file(value.lstrip("@")) or _resolves_to_secret(value.lstrip("@"), workspace, budget))
        for value in args.values()
    )


#: How many paths one command may have resolved on disk (symlinks followed) before the rest are read
#: only as written. A cap on the work, never on the answer: the literal reading still applies.
_MAX_RESOLVED = 200


def _resolves_to_secret(token: str, workspace: Path | str | None, budget: list[int]) -> bool:
    """Whether a path relative to ``workspace`` is, once resolved, a credential file.

    ``notes.txt`` that is a link to ``~/.ssh/id_rsa`` reads as an ordinary name; on disk it is the
    key (second review of the P0-01 fix). Only when the workspace is known and the path exists; a
    path that cannot be resolved keeps its literal reading, which the caller has already made.
    """
    from chimera.api.bridge_routes import is_secret_file

    if workspace is None or budget[0] <= 0 or not token or len(token) > 260 or "://" in token:
        return False
    if token.startswith("-") or not re.search(r"[./\\]", token):
        return False
    budget[0] -= 1
    try:
        root = Path(workspace).resolve()
        candidate = Path(token).expanduser()
        path = candidate if candidate.is_absolute() else root / candidate
        if not (path.exists() or path.is_symlink()):
            return False
        resolved = path.resolve()
    except (OSError, ValueError, RuntimeError):
        return False
    try:
        # Inside the workspace, judge the part under it: a project kept under a folder named like a
        # credential would otherwise refuse every file in it.
        shown = resolved.relative_to(root).as_posix()
    except ValueError:
        shown = str(resolved)
    return is_secret_file(shown)


def _has_secret_disclosure(sets: list[list[str]], workspace: Path | str | None = None) -> bool:
    """(b): a secret file read locally, or named in the same command as an outbound executable.

    Linear in the tokens: the last secret's position is enough to know whether a reader comes
    before one, and the old ``any(secret > index ...)`` per token was quadratic.
    """
    budget = [_MAX_RESOLVED]
    for tokens in sets:
        last_secret = -1
        for index, token in enumerate(tokens):
            if _is_secret_token(token) or _resolves_to_secret(_upload_value(token), workspace, budget):
                last_secret = index
        if last_secret < 0:
            continue
        for index, token in enumerate(tokens):
            executable = _basename(token)
            if index < last_secret and (executable in _READERS or executable in {"powershell", "pwsh"}):
                return True
            if _is_outbound(tokens, index):
                return True
    return False


def _upload_value(token: str) -> str:
    """The path an upload token names: ``@x``, ``f=@x``, ``f=<x``, ``<x``; else the token."""
    return re.split(r"[=@<]", token)[-1] if re.search(r"[=@<]", token) else token


def _is_secret_token(token: str) -> bool:
    """A token that names a secret file, bare or as an upload value: ``.env``, ``@.env``, ``f=@.env``.

    Also the value glued to a short option, in a cluster too: ``-T.env``, ``-d@.env``, ``-sT.env``
    (every suffix after the dash is read, because the option letters and the value run together),
    and ``<`` as the separator curl's ``-F 'file=<.env'`` and a redirect ``<.env`` use.
    """
    from chimera.api.bridge_routes import is_secret_file

    candidates = [token]
    if len(token) > 2 and token[0] == "-" and token[1] != "-":
        candidates.extend(token[start:] for start in range(2, min(len(token), 8)))
    return any(
        is_secret_file(piece) for candidate in candidates for piece in re.split(r"[=@<]", candidate) if piece
    )


def _is_outbound(tokens: list[str], index: int) -> bool:
    executable = _basename(tokens[index])
    if executable == "git":
        return _git_subcommand(tokens, index + 1) in {"push", "send-email"}
    return executable in _OUTBOUND


def _encoded_powershell(sets: list[list[str]], workspace: Path | str | None = None) -> bool:
    """(b): ``-EncodedCommand`` in any spelling PowerShell accepts (``-e``, ``-ec``, ``-enc``, ...).

    Read from the end, so a PowerShell word is matched with any encoded flag after it in one pass.
    """
    for tokens in sets:
        flag_after = False
        for token in reversed(tokens):
            if _basename(token) in {"powershell", "pwsh"} and flag_after:
                return True
            if _is_encoded_flag(token):
                flag_after = True
    return False


def _is_encoded_flag(arg: str) -> bool:
    """``-EncodedCommand``, a prefix of it, its alias ``-ec``, or ``-EncodedArguments``; ``-`` or ``/``."""
    lowered = arg.casefold().split(":", 1)[0]
    if len(lowered) < 2 or lowered[0] not in "-/":
        return False
    name = lowered[1:]
    return (
        name == "ec"
        or "encodedcommand".startswith(name)
        or (len(name) >= len("encodeda") and "encodedarguments".startswith(name))
    )


def _rewrites_default_branch(sets: list[list[str]], workspace: Path | str | None = None) -> bool:
    """(c): a git push that rewrites or deletes main/master on a remote, or may, or the GitHub API doing so.

    "Or may", fail-closed: a ``git -c``/``--config-env`` before a push (``remote.origin.push``,
    ``remote.origin.mirror`` decide what it pushes), a subcommand git does not ship (an alias can be
    a force push), and ``git config`` writing a remote's push or mirror setting or an alias.
    """
    for tokens in sets:
        for index, token in enumerate(tokens):
            executable = _basename(token)
            if executable == "git" and _git_may_rewrite(tokens, index):
                return True
            if executable == "gh" and _gh_api_rewrites_default_ref(tokens, index):
                return True
    return False


def _git_may_rewrite(tokens: list[str], index: int) -> bool:
    subcommand = _git_subcommand_index(tokens, index + 1)
    if subcommand is None:
        return False
    configured = any(
        arg == "-c" or arg.startswith("-c") or arg.split("=", 1)[0] == "--config-env"
        for arg in tokens[index + 1:subcommand]
    )
    name = tokens[subcommand].casefold()
    if name in _SHELL_OPERATOR_TOKENS:
        return False
    if name == "push":
        return configured or _push_rewrites_default_branch(tokens[subcommand + 1:])
    # Only where `git` is the program that runs: in `grep -r git src` the word is an argument.
    if not _in_command_position(tokens, index):
        return False
    if name == "config":
        return _config_arms_a_push(tokens[subcommand + 1:])
    return name not in _GIT_SUBCOMMANDS


def _in_command_position(tokens: list[str], index: int) -> bool:
    """Whether ``tokens[index]`` starts a simple command: first, after an operator, or after a prefix."""
    if index == 0:
        return True
    previous = tokens[index - 1]
    return (
        previous in _SHELL_OPERATOR_TOKENS
        or previous[-1:] in {";", "|", "&"}
        or previous in {"then", "do", "else", "!", "time"}
        or is_wrapper_word(previous)
    )


def _config_arms_a_push(args: list[str]) -> bool:
    """``git config remote.origin.push +HEAD:main``, ``remote.x.mirror true``, ``alias.p ...``."""
    for arg in args:
        if arg in _SHELL_OPERATOR_TOKENS:
            return False
        key = arg.casefold()
        if re.fullmatch(r"remote\..+\.(push|mirror)", key) or key.startswith("alias."):
            return True
    return False


def _push_rewrites_default_branch(args: list[str]) -> bool:
    """Decide from the push's own arguments; the flags may come before, between or after the refspecs.

    The push ends at the first shell operator: in ``git push --force origin; true`` the ``true`` is
    the next command, not a refspec (second review of the P0-01 fix).
    """
    force = delete = mass = all_branches = repo_flag = False
    positional: list[str] = []
    skip_value = False
    for arg in args:
        if arg in _SHELL_OPERATOR_TOKENS:
            break
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


#: The floor's named checks on the token sets of a shell or code command, each with the category the
#: security card names it by. Run in this order by :func:`shell_floor`, after its own ``unreadable``.
_SHELL_FLOOR: tuple[tuple[str, Callable[[list[list[str]], Path | str | None], bool]], ...] = (
    ("secret_disclosure", _has_secret_disclosure),
    ("encoded_powershell", _encoded_powershell),
    ("default_branch_rewrite", _rewrites_default_branch),
)

#: Words git ships as subcommands (porcelain, common plumbing and the helpers people type). Anything
#: else is an alias or an external `git-<name>`, which can do whatever it was defined to, a force push
#: among them, so it is asked about (fail-closed).
_GIT_SUBCOMMANDS = frozenset({
    "add", "am", "annotate", "apply", "archive", "bisect", "blame", "branch", "bundle", "cat-file",
    "check-attr", "check-ignore", "check-mailmap", "check-ref-format", "checkout", "checkout-index",
    "cherry", "cherry-pick", "citool", "clean", "clone", "column", "commit", "commit-graph",
    "commit-tree", "config", "count-objects", "credential", "describe", "diff", "diff-files",
    "diff-index", "diff-tree", "difftool", "fast-export", "fast-import", "fetch", "fetch-pack",
    "filter-branch", "fmt-merge-msg", "for-each-ref", "for-each-repo", "format-patch", "fsck", "gc",
    "get-tar-commit-id", "grep", "gui", "hash-object", "help", "init", "instaweb", "interpret-trailers",
    "log", "ls-files", "ls-remote", "ls-tree", "mailinfo", "mailsplit", "maintenance", "merge",
    "merge-base", "merge-file", "merge-tree", "mergetool", "mktag", "mktree", "mv", "name-rev",
    "notes", "pack-objects", "pack-refs", "prune", "pull", "push", "range-diff", "read-tree",
    "rebase", "reflog", "remote", "repack", "replace", "request-pull", "rerere", "reset", "restore",
    "rev-list", "rev-parse", "revert", "rm", "send-email", "shortlog", "show", "show-branch",
    "show-ref", "sparse-checkout", "stage", "stash", "status", "stripspace", "submodule", "switch",
    "symbolic-ref", "tag", "unpack-objects", "update-index", "update-ref", "var", "verify-commit",
    "verify-pack", "verify-tag", "version", "whatchanged", "worktree", "write-tree", "lfs",
})

#: What separates two commands, as a token of the reading split at operators.
_SHELL_OPERATOR_TOKENS = frozenset({";", "&&", "||", "|", "&"})

#: Words built at run time: a command substitution, a backtick, ANSI-C quoting, a parameter expansion,
#: a line continuation. The tokeniser cannot know what they become (``+ma$(printf in)`` is ``+main``).
_RUNTIME_WORD = re.compile(r"\$\(|`|\$'|\$\{|\\[ \t]*\r?\n")


def _builds_words_at_run_time(command: str, sets: list[list[str]]) -> bool:
    """A word built at run time in a command the floor reads for (b) or (c)."""
    if not _RUNTIME_WORD.search(command):
        return False
    for tokens in sets:
        for index, token in enumerate(tokens):
            executable = _basename(token)
            if executable in {"powershell", "pwsh"} or executable in _OUTBOUND:
                return True
            if executable == "git":
                subcommand = _git_subcommand_index(tokens, index + 1)
                if subcommand is not None and tokens[subcommand].casefold() in {"push", "config", "send-email"}:
                    return True
    return False


class _Unreadable(Exception):
    """A command the floor cannot read: :func:`shell_floor` calls it ``unreadable``."""


class _TooDeepToRead(_Unreadable):
    """A command that wraps scripts in scripts past the cap."""


#: How many scripts one command may be unwrapped into before it is given up on.
_MAX_EXPANSIONS = 64
#: How many words that may start a wrapper one reading may hold before it is given up on: each one
#: is read to the end of the command, so a command of thousands of them was quadratic.
_MAX_WRAPPER_WORDS = 256


def _expanded_token_sets(
    command: str, depth: int = 0, budget: list[int] | None = None, *, strict: bool = True
) -> list[list[str]]:
    """The command's token sets, plus every script a shell wrapper runs, recursively.

    A script found past ``_MAX_NESTING``, or more scripts than ``_MAX_EXPANSIONS``, or more wrapper
    words than ``_MAX_WRAPPER_WORDS``, is not read: it raises, and the floor's answer to a call it
    cannot read is no (``eval eval eval ... rm -rf X`` was released because the reading stopped
    quietly at the cap, and ``eval`` x40 took 18 s).
    """
    remaining = [_MAX_EXPANSIONS] if budget is None else budget
    sets: list[list[str]] = []
    for tokens, posix in _command_token_sets(command, strict=strict):
        sets.append(tokens)
        wrappers = 0
        for index in range(len(tokens)):
            if not may_wrap(tokens[index]):
                continue
            if index and is_wrapper_word(tokens[index - 1]):
                continue  # the wrapper before it already unwrapped this one (and a long chain stays linear)
            wrappers += 1
            if wrappers > _MAX_WRAPPER_WORDS:
                raise _TooDeepToRead
            nested = nested_script(tokens[index:])
            if not nested:
                continue
            remaining[0] -= 1
            if depth >= _MAX_NESTING or remaining[0] < 0:
                raise _TooDeepToRead
            # A script cut out of the non-POSIX reading may keep a stray quote that no shell would
            # see; only one cut out of the POSIX reading must read, or the command is unreadable.
            sets.extend(_expanded_token_sets(nested, depth + 1, remaining, strict=posix))
    return sets


def _command_token_sets(command: str, *, strict: bool = True) -> list[tuple[list[str], bool]]:
    """Tokenize shell/PowerShell spellings, normalizing executable and grouping syntax.

    A command no tokenisation can read (an unterminated quote, one inside a ``#`` comment included)
    raises :class:`_Unreadable` when ``strict``. It used to yield no tokens, and no tokens read as
    "nothing dangerous": ``# '`` in front of a force push to main released it (second review of the
    P0-01 fix). Each reading comes with whether it is the POSIX one.
    """
    normalized = re.sub(r"[(){}\[\],$`]+", " ", command)
    parsed: list[tuple[list[str], bool]] = []
    seen: list[list[str]] = []
    failed = 0
    for posix in (True, False):
        try:
            tokens = [token.strip("\\\"'") for token in shlex.split(normalized, posix=posix) if token.strip("\\\"'")]
        except ValueError:
            failed += 1
            continue
        for candidate in (tokens, _split_at_operators(tokens)):
            if candidate and candidate not in seen:
                seen.append(candidate)
                parsed.append((candidate, posix))
    if strict and failed == 2 and normalized.strip():
        raise _Unreadable
    return parsed


#: Shell control operators. `shlex` keeps them inside a word, so in `git push --force origin feat/x
#: main; true` the last refspec was `main;`, compared with `main` and released (review of the P0-01
#: fix). The split reading is ADDED to the plain one, never put in its place: every check here only
#: refuses, so one more reading can only ask more.
_SHELL_OPERATORS = re.compile(r"(\|\||&&|[;|&\r\n])")


def _split_at_operators(tokens: list[str]) -> list[str]:
    pieces: list[str] = []
    for token in tokens:
        for piece in _SHELL_OPERATORS.split(token):
            piece = piece.strip().strip("\\\"'")
            if piece:
                pieces.append(piece)
    return pieces


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
