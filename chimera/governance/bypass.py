"""Shared safety floor for the owner's convenience permission switch."""

from __future__ import annotations

import re
import shlex
from typing import Any


def bypass_safe(tool: str, args: dict[str, Any], kernel_evaluation: Any = None) -> bool:
    """Whether a permission bypass may release this action and its kernel REVIEW.

    A kernel REVIEW that names external publication, secret handling, or exfiltration always
    remains an owner question. Other REVIEWs (such as running tests with tainted inputs) may use
    the explicit session bypass, subject to the same action safety floor as the taint ledger.
    """
    from chimera.governance.ledger import describe_call
    from chimera.governance.policy import Decision, RuleSet

    reason = str(getattr(kernel_evaluation, "reason", "")).casefold()
    rule = str(getattr(kernel_evaluation, "rule", "")).casefold()
    decision = getattr(kernel_evaluation, "decision", None)
    if decision is not None and decision != Decision.REVIEW:
        kernel_evaluation = None
        reason = rule = ""
    category = str(getattr(kernel_evaluation, "category", "")).casefold()
    evaluation = f"{reason} {rule} {category}"
    if getattr(kernel_evaluation, "decision", None) == Decision.REVIEW and category in {
        "external", "secret", "exfiltration", "credential",
    }:
        return False
    evaluation = f"{reason} {rule}"
    if any(term in evaluation for term in (
        "external", "publish", "force push", "secret", "credential", "exfiltrat",
        "upload", "send content", "data exfiltration", "sending local file contents",
        "local file", "remote host",
    )):
        return False

    from chimera.governance.ledger_tool import sends_to_someone

    if tool == "open_pull_request" or sends_to_someone(tool):
        return False
    action = describe_call(tool, args)
    command = " ".join(str(args[key]) for key in ("command", "cmd", "code") if key in args)
    command_text = command or action
    if tool in {"run_shell", "execute_code", "code_interpreter"}:
        from chimera.api.bridge_routes import is_secret_file

        token_sets = _command_token_sets(command_text)
        if any(
            _basename(token) in {"powershell", "pwsh"} and any(
                arg.casefold() in {"-encodedcommand", "-enc", "-e"} for arg in tokens[index + 1:]
            )
            for tokens in token_sets for index, token in enumerate(tokens)
        ):
            return False
        for tokens in token_sets:
            if any(is_secret_file(token.lstrip("@")) for token in tokens):
                return False
            for index, token in enumerate(tokens):
                lower = _basename(token)
                if (
                    lower in {"bash", "sh", "zsh"}
                    and _next(tokens, index).startswith("-")
                    and "c" in _next(tokens, index)[1:]
                    and _nested_danger(tokens[index + 2:])
                ):
                    return False
                if (
                    lower == "cmd"
                    and _next(tokens, index) in {"/c", "/k"}
                    and _nested_danger(tokens[index + 2:])
                ):
                    return False
                if lower in {"powershell", "pwsh"}:
                    for flag_index, flag in enumerate(tokens[index + 1:], index + 1):
                        if flag.casefold() in {"-encodedcommand", "-enc", "-e"}:
                            return False
                        if flag.casefold() in {"-command", "-c"} and _nested_danger(tokens[flag_index + 1:]):
                            return False
                if lower == "git" and _git_subcommand(tokens, index + 1) == "send-email":
                    return False
                if (
                    lower == "gh"
                    and _next(tokens, index) == "repo"
                    and _next(tokens, index + 1) == "create"
                    and "--push" in tokens[index + 2:]
                ):
                    return False
                if lower == "glab" and _next(tokens, index) in {"mr", "project"} and _next(tokens, index + 1) == "create":
                    return False
                if lower == "aws" and tokens[index + 1:index + 3] == ["s3", "cp"]:
                    return False
                if lower == "gcloud" and tokens[index + 1:index + 3] == ["storage", "cp"]:
                    return False
                if lower == "az" and tokens[index + 1:index + 4] == ["storage", "blob", "upload"]:
                    return False
                if lower in {"kubectl", "terraform"} and _next(tokens, index) == "apply":
                    return False
                if lower == "stripe" and "create" in tokens[index + 1:]:
                    return False
                if lower == "vercel" and "--prod" in tokens[index + 1:]:
                    return False
                if lower == "netlify" and _next(tokens, index) == "deploy" and "--prod" in tokens[index + 2:]:
                    return False
                if lower == "git":
                    subcommand = _git_subcommand(tokens, index + 1)
                    if subcommand == "push":
                        return False
                if lower in {"npm", "pnpm", "yarn"} and _next(tokens, index) == "publish":
                    return False
                if lower == "twine" and _next(tokens, index) == "upload":
                    return False
                if lower == "docker" and _next(tokens, index) == "push":
                    return False
                if lower == "hub" and _next(tokens, index) == "pull-request":
                    return False
                if lower == "gh":
                    if _next(tokens, index) == "api" and _http_method(tokens[index + 2:]):
                        return False
                    if _next(tokens, index) == "pr" and _next(tokens, index + 1) in {"create", "merge"}:
                        return False
                    if _next(tokens, index) == "release" and _next(tokens, index + 1) == "create":
                        return False
                if lower == "curl" and _curl_sends(tokens[index + 1:]):
                    return False
                if lower == "wget" and _wget_posts(tokens[index + 1:]):
                    return False
                if lower in {"rsync", "scp"} and any(_remote_target(arg) for arg in tokens[index + 1:]):
                    return False

    if tool == "browser":
        from chimera.tools.browser_chrome import _IRREVERSIBLE_ACTION, _IRREVERSIBLE_CJK

        if _IRREVERSIBLE_ACTION.search(command_text) or _IRREVERSIBLE_CJK.search(command_text):
            return False

    verdict = RuleSet().evaluate(action)
    if verdict is not None and verdict.decision in (Decision.BLOCK, Decision.WARN):
        return False
    from chimera.api.bridge_routes import is_secret_file
    path_keys = {"path", "file_path", "directory", "cwd", "working_directory", "executable"}
    return not any(is_secret_file(str(args[key])) for key in path_keys if key in args)


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
    normalized = re.sub(r"[(){}$`]+", " ", command)
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


def _nested_danger(tokens: list[str]) -> bool:
    # The command argument was shell-tokenized by the outer parser; joining preserves words while
    # stripping only quote-grouping syntax for the recursive parse.
    return any(_tokens_are_dangerous(nested) for nested in _command_token_sets(" ".join(tokens)))

def _tokens_are_dangerous(tokens: list[str]) -> bool:
    from chimera.api.bridge_routes import is_secret_file

    for index, token in enumerate(tokens):
        executable = _basename(token)
        if executable in {"git"} and _git_subcommand(tokens, index + 1) in {"push", "send-email"}:
            return True
        if executable in {"cat", "type", "get-content", "gc"} and any(is_secret_file(arg.lstrip("@")) for arg in tokens[index + 1:]):
            return True
        if executable == "curl" and _curl_sends(tokens[index + 1:]):
            return True
        if executable == "gh" and _next(tokens, index) == "repo" and _next(tokens, index + 1) == "create" and "--push" in tokens[index + 2:]:
            return True
        if executable == "glab" and _next(tokens, index) in {"mr", "project"} and _next(tokens, index + 1) == "create":
            return True
        if executable == "aws" and tokens[index + 1:index + 3] == ["s3", "cp"]:
            return True
        if executable == "gcloud" and tokens[index + 1:index + 3] == ["storage", "cp"]:
            return True
        if executable == "az" and tokens[index + 1:index + 4] == ["storage", "blob", "upload"]:
            return True
        if executable in {"kubectl", "terraform"} and _next(tokens, index) == "apply":
            return True
        if executable == "stripe" and "create" in tokens[index + 1:]:
            return True
        if executable == "vercel" and "--prod" in tokens[index + 1:]:
            return True
        if executable == "netlify" and _next(tokens, index) == "deploy" and "--prod" in tokens[index + 2:]:
            return True
        if executable == "glab" and _next(tokens, index) in {"mr", "project"} and _next(tokens, index + 1) == "create":
            return True
        if executable == "aws" and tokens[index + 1:index + 3] == ["s3", "cp"]:
            return True
        if executable == "gcloud" and tokens[index + 1:index + 3] == ["storage", "cp"]:
            return True
        if executable == "az" and tokens[index + 1:index + 4] == ["storage", "blob", "upload"]:
            return True
        if executable in {"kubectl", "terraform"} and _next(tokens, index) == "apply":
            return True
        if executable == "stripe" and "create" in tokens[index + 1:]:
            return True
        if executable == "vercel" and "--prod" in tokens[index + 1:]:
            return True
        if executable == "netlify" and _next(tokens, index) == "deploy" and "--prod" in tokens[index + 2:]:
            return True
        if executable == "gh" and _next(tokens, index) == "repo" and _next(tokens, index + 1) == "create" and "--push" in tokens[index + 2:]:
            return True
        if (
            executable in {"bash", "sh", "zsh"}
            and _next(tokens, index).startswith("-")
            and "c" in _next(tokens, index)[1:]
            and _nested_danger(tokens[index + 2:])
        ):
            return True
        if (
            executable == "cmd"
            and _next(tokens, index) in {"/c", "/k"}
            and _nested_danger(tokens[index + 2:])
        ):
            return True
        if executable in {"powershell", "pwsh"}:
            if any(arg.casefold() in {"-encodedcommand", "-enc", "-e"} for arg in tokens[index + 1:]):
                return True
            if any(flag.casefold() in {"-command", "-c"} and _nested_danger(tokens[pos + 1:]) for pos, flag in enumerate(tokens[index + 1:], index + 1)):
                return True
    return False


def _next(tokens: list[str], index: int) -> str:
    return tokens[index + 1].casefold() if index + 1 < len(tokens) else ""


def _git_subcommand(tokens: list[str], index: int) -> str:
    while index < len(tokens):
        arg = tokens[index]
        if arg in {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}:
            index += 2
            continue
        if arg.startswith("--git-dir=") or arg.startswith("--work-tree=") or arg.startswith("--namespace="):
            index += 1
            continue
        if arg.startswith("-"):
            index += 1
            continue
        return arg.casefold()
    return ""


def _http_method(tokens: list[str]) -> bool:
    for index, arg in enumerate(tokens):
        if arg in {"-X", "--method"} and _next(tokens, index).upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            return True
        if arg.startswith("--method=") and arg.split("=", 1)[1].upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            return True
    return False


def _curl_sends(tokens: list[str]) -> bool:
    for index, arg in enumerate(tokens):
        lower = arg.casefold()
        if lower in {"--upload-file", "-t", "-f", "--form", "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode"}:
            if lower in {"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode"} and not _next(tokens, index).startswith("@"):
                continue
            if lower in {"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode"} and _next(tokens, index) == "@-":
                return True
            return True
        if any(lower.startswith(name + "=") for name in ("--upload-file", "--form", "-f")):
            return True
        if any(lower.startswith(name + "=") and lower.split("=", 1)[1].startswith("@") for name in ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode")):
            return True
    return False


def _wget_posts(tokens: list[str]) -> bool:
    return any(
        arg.casefold() in {"--post-file", "--post-data", "--data-binary"} or arg.casefold().startswith(("--post-file=", "--post-data=", "--data-binary="))
        for arg in tokens
    )


def _remote_target(token: str) -> bool:
    return bool(re.match(r"^(?:[^/@:]+@)?[^/:]+:.+", token))
