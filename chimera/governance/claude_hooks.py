"""Hooks in the Claude Code format, read from the project's and the user's ``.claude`` settings.

Owner's decision of 2026-11-11 (study 03-claude-code-desktop, item 13): compatibility with Claude
Code is ON by default, hooks included — so a repository that already carries ``.claude/settings.json``
behaves here as it does there, to the extent that behaviour can be kept without widening anything.
The two guarantees of ``docs/hooks-threat-model.md`` are kept whole, and the section "Claude-format
hooks" there names what is new:

* **A hook only tightens.** It can deny, ask or annotate; never release. ``permissionDecision:
  "allow"`` (and the old ``"approve"``) is read as "no objection" and recorded as such — the call
  still meets the kernel, the denylist and the taint ledger, so it never answers a card for the
  person. ``updatedInput`` is applied only when it provably NARROWS the call (a list cut down to
  some of its own items); in any doubt it is ignored, and the receipt says so.
* **A hook runs in the sandbox**, never on the host unless the owner set ``CHIMERA_HOOKS_HOST_EXEC``.
  The environment is the sandbox's own, which is ``scrubbed_environ``: no key reaches a hook.

What is different from the owner's own file (``chimera-hooks.json``):

* The source can be a REPOSITORY, which is third-party code. Each origin (``repo``, ``user``) has its
  own owner-only switch, and the Settings screen lists every hook that was loaded with where it came
  from. The agent cannot flip either switch (``bridge_routes.GUARD_SETTINGS``).
* **A repository's hooks never run on the host**, whatever ``CHIMERA_HOOKS_HOST_EXEC`` says; the
  user's own run there only under their own switch, ``CHIMERA_CLAUDE_USER_HOOKS_HOST_EXEC``. The
  hook sandbox has no network, whatever the agent's own sandbox is configured to.
* **A repository's hooks load only after the owner confirmed them on the screen**, and again whenever
  the files change (trust on the bytes, stored in the data folder the agent cannot write). Neither the
  OS sandbox (which restricts writes and the network, not reads) nor the temp-folder cwd keeps a hook
  from READING the disk, so confirmation is what stands between a cloned repository and the owner's
  files; under Docker, where only the temporary folder is mounted, it is defence in depth.
* A hook the sandbox cannot start (no isolating sandbox, no host exec) is a FAILED hook, not a
  refusal of every call: it follows ``onFailure`` (continue unless ``"block"``). Claude Code's own
  default is to go on; making a repository's guard brick the agent on a machine without a sandbox
  would make compatibility unusable exactly where most owners are (Windows).
* The working folder of a hook is the same empty temporary one the owner's hooks get, NOT the
  project: a repository hook with write access to the project could rewrite ``.claude/settings.json``
  (A1). The project is named in the payload's ``cwd`` and in ``CLAUDE_PROJECT_DIR``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.governance.governed_tool import elide_values
from chimera.telemetry import get_logger

_log = get_logger("governance.claude_hooks")

CLAUDE = "claude"
ORIGIN_REPO, ORIGIN_USER = "repo", "user"

#: Claude Code's events that map onto the loop, and the tool events' names in `hooks.py`.
PRE_TOOL_USE, POST_TOOL_USE = "PreToolUse", "PostToolUse"
USER_PROMPT, STOP, SUBAGENT_STOP = "UserPromptSubmit", "Stop", "SubagentStop"
SESSION_START, NOTIFICATION, PRE_COMPACT = "SessionStart", "Notification", "PreCompact"
LIFECYCLE_EVENTS = (USER_PROMPT, STOP, SUBAGENT_STOP, SESSION_START, NOTIFICATION, PRE_COMPACT)
#: Events Claude Code has and this loop has no place for. Listed as skipped, never silently dropped.
UNMAPPED_EVENTS = frozenset(
    {"PermissionRequest", "PermissionDenied", "SessionEnd", "PreModelSwitch", "PostModelSwitch",
     "PostToolUseFailure", "SubagentStart", "PostCompact", "InstructionsLoaded", "ConfigChange",
     "FileChanged", "CwdChanged", "WorktreeCreate", "WorktreeRemove", "TaskCreated", "TaskCompleted",
     "Elicitation", "ElicitationResult", "TeammateIdle", "StopFailure"}
)

DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 60
#: How many hooks of one event run, and how many run at the same time. A repository that registers
#: hundreds must not turn every tool call into a fork bomb.
MAX_HOOKS_PER_EVENT = 24
MAX_PARALLEL = 4
#: Wall-clock budget for ALL the hooks of one event, however many run: a hook that has not started
#: when it is spent is skipped, and one that has is cut down to what is left.
EVENT_BUDGET_S = 45.0
#: After this many timeouts the same hook is skipped (the breaker is open) for the life of the
#: process, so a hook that hangs on every call costs the first few calls and not every one after.
BREAKER_TIMEOUTS = 3
_BREAKER: dict[tuple[str, str], int] = {}
_BREAKER_LOCK = threading.Lock()
#: How many times each hook was skipped without running since this process started (no sandbox
#: that isolates and no host exec for it, the breaker open, the event's time spent, or a sandbox
#: that could not start). Shown on the Hooks screen beside the hook: a guard that is listed and
#: skipped is the false comfort the screen exists to name (audit 2026-11, a07-12).
_SKIPPED: dict[tuple[str, str], int] = {}
_MAX_TEXT = 2000
_MAX_PROMPT = 8000

#: What Claude Code calls our tools, so a matcher written for `Bash` finds `run_shell`. A matcher
#: that names either spelling matches.
CLAUDE_TOOL_NAMES = {
    "run_shell": "Bash", "read_file": "Read", "write_file": "Write", "edit_file": "Edit",
    "apply_patch": "Edit", "edit_batch": "Edit", "glob": "Glob", "grep": "Grep",
    "list_dir": "LS", "web_search": "WebSearch", "http_get": "WebFetch", "todo_write": "TodoWrite",
}
_SIMPLE_MATCHER = re.compile(r"^[A-Za-z0-9_|]+$")


def claude_tool_name(tool: str) -> str:
    return CLAUDE_TOOL_NAMES.get(tool, tool)


def compile_matcher(raw: Any) -> re.Pattern[str] | None:
    """``None`` for "every tool" (``""``, ``"*"``, absent); otherwise a pattern.

    Claude Code reads a matcher made only of letters, digits, ``_`` and ``|`` as an exact list of
    names, and anything else as a regular expression. Both are kept: the first is anchored, the
    second searched.
    """
    text = str(raw or "").strip()
    if text in ("", "*"):
        return None
    if _SIMPLE_MATCHER.match(text):
        return re.compile(rf"^(?:{text})$", re.IGNORECASE)
    return re.compile(text, re.IGNORECASE)


def matcher_hits(matcher: re.Pattern[str] | None, tool: str) -> bool:
    if matcher is None:
        return True
    return any(matcher.search(n) for n in (tool, claude_tool_name(tool)))


# --- reading the settings files -----------------------------------------------------------------


@dataclass(frozen=True)
class HookSource:
    """One settings file, as the Settings screen lists it."""

    origin: str
    path: str
    exists: bool = False
    enabled: bool = True
    sha256: str = ""
    hooks: int = 0
    error: str = ""
    skipped: tuple[str, ...] = ()
    #: For a repo file only: ``trusted``, ``new`` (never confirmed) or ``changed`` (confirmed once, the
    #: bytes differ now). Anything but ``trusted`` means its hooks were NOT loaded.
    trust: str = ""


# --- trusting a repository's hooks --------------------------------------------------------------

TRUST_FILE = "claude_hooks_trust.json"
TRUSTED, NEW, CHANGED = "trusted", "new", "changed"


MAX_REFERENCED_FILES = 20
MAX_REFERENCED_BYTES = 256 * 1024


def referenced_files(workspace: Path, commands: list[str]) -> dict[str, Path]:
    """The files inside the project that the hook commands NAME (``./scripts/check.sh``,
    ``python tools/lint.py``, ``--config=hooks/x.json``, ``$CLAUDE_PROJECT_DIR/...``), by relative path.

    A token counts when it resolves to an existing regular file under the project root and is at most
    :data:`MAX_REFERENCED_BYTES`; at most :data:`MAX_REFERENCED_FILES` are taken. This is a reading of
    what the command SAYS: a file such a script sources, imports or downloads is not seen (documented
    in ``docs/hooks-threat-model.md``), nor is a symlink whose target lies outside the project."""
    try:
        root = Path(workspace).resolve()
    except OSError:
        return {}
    found: dict[str, Path] = {}
    for command in commands:
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        for raw in tokens:
            token = raw.replace("${CLAUDE_PROJECT_DIR}", str(root)).replace("$CLAUDE_PROJECT_DIR", str(root))
            for candidate in {token, token.split("=", 1)[-1]}:
                if not candidate or candidate.startswith("-"):
                    continue
                path = Path(candidate)
                try:
                    path = (path if path.is_absolute() else root / path).resolve()
                    inside = root in path.parents
                    if inside and path.is_file() and path.stat().st_size <= MAX_REFERENCED_BYTES:
                        found[path.relative_to(root).as_posix()] = path
                except (OSError, ValueError):
                    continue
                if len(found) >= MAX_REFERENCED_FILES:
                    return found
    return found


def repo_fingerprint(workspace: Path) -> str:
    """One digest over what the owner is asked to trust: the project's two settings files (their
    bytes, in a fixed order) AND the project files their commands name (bytes, by relative path).
    ``""`` when no settings file exists. Any edit to any of them voids the confirmation, so a script
    changed under an unchanged ``settings.json`` is ``changed`` too."""
    parts = []
    for origin, path in settings_files(workspace):
        if origin != ORIGIN_REPO:
            continue
        try:
            parts.append(hashlib.sha256(path.read_bytes()).hexdigest())
        except OSError:
            parts.append("-")
    if all(p == "-" for p in parts):
        return ""
    hooks, _ = discover(workspace, repo=True, user=False, repo_trust=TRUSTED)
    for rel, path in sorted(referenced_files(workspace, [h.command for h in hooks]).items()):
        try:
            parts.append(f"{rel}:{hashlib.sha256(path.read_bytes()).hexdigest()}")
        except OSError:
            parts.append(f"{rel}:-")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _trust_key(workspace: Path) -> str:
    try:
        return str(Path(workspace).resolve()).replace("\\", "/").lower()
    except OSError:
        return str(workspace).lower()


def _read_trust(home: Path) -> dict[str, str]:
    try:
        data = json.loads((Path(home) / TRUST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def trust_state(home: Path, workspace: Path) -> str:
    """``trusted`` / ``new`` / ``changed`` for the project's current settings bytes."""
    want = repo_fingerprint(workspace)
    if not want:
        return TRUSTED  # nothing to run, nothing to confirm
    seen = _read_trust(home).get(_trust_key(workspace))
    if seen is None:
        return NEW
    return TRUSTED if seen == want else CHANGED


class TrustMismatch(ValueError):
    """What the owner was shown is not what is on disk now."""


def trust_repo(home: Path, workspace: Path, shown: str | None = None) -> str:
    """The owner confirmed the project's hooks. Called only by the owner's screen route.

    ``shown`` is the fingerprint the screen displayed next to the button. When given, it must equal
    the current one, or nothing is written (:class:`TrustMismatch`): a file edited between looking and
    confirming is not confirmed. What is stored is ``shown`` itself, so an edit in the instant after
    the check leaves the state ``changed`` rather than trusted."""
    from chimera.core.filelock import atomic_write_text

    current = repo_fingerprint(workspace)
    if shown is not None and shown != current:
        raise TrustMismatch("the project's hooks changed after they were shown")
    store = _read_trust(home)
    store[_trust_key(workspace)] = shown if shown is not None else current
    Path(home).mkdir(parents=True, exist_ok=True)
    atomic_write_text(Path(home) / TRUST_FILE, json.dumps(store, indent=2))
    return TRUSTED


def user_settings_path(home: Path | None = None) -> Path:
    """``~/.claude/settings.json``, honouring ``CLAUDE_CONFIG_DIR`` as Claude Code does."""
    base = "" if home is not None else os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    root = Path(base).expanduser() if base else (home or Path.home()) / ".claude"
    return root / "settings.json"


def settings_files(workspace: Path, user_home: Path | None = None) -> list[tuple[str, Path]]:
    """The three files, repository first. ``settings.local.json`` is the same origin as the
    committed one: both live in the project and both can be written by whoever wrote the project."""
    ws = Path(workspace)
    return [
        (ORIGIN_REPO, ws / ".claude" / "settings.json"),
        (ORIGIN_REPO, ws / ".claude" / "settings.local.json"),
        (ORIGIN_USER, user_settings_path(user_home)),
    ]


def parse_claude_settings(
    data: Any, *, origin: str, source: str, file_sha: str
) -> tuple[list[Any], list[str]]:
    """``(hooks, skipped)`` from a parsed settings file. Never raises: what cannot be read is named
    in ``skipped`` so the owner sees a hook was dropped, instead of believing a guard exists."""
    from chimera.governance.hooks import Hook

    skipped: list[str] = []
    if not isinstance(data, dict):
        return [], ["the file is not a JSON object"]
    if data.get("disableAllHooks") is True:
        return [], ["disableAllHooks is true"]
    section = data.get("hooks")
    if section is None:
        return [], []
    if not isinstance(section, dict):
        return [], ['"hooks" is not an object']
    out: list[Hook] = []
    for event, groups in section.items():
        if event in UNMAPPED_EVENTS:
            skipped.append(f"{event}: no place for this event in the loop")
            continue
        if event not in (PRE_TOOL_USE, POST_TOOL_USE, *LIFECYCLE_EVENTS):
            skipped.append(f"{event}: not a known event")
            continue
        if not isinstance(groups, list):
            skipped.append(f"{event}: expected a list of matchers")
            continue
        for group in groups:
            out.extend(_parse_group(event, group, origin, source, file_sha, skipped))
    return out, skipped


def _parse_group(
    event: str, group: Any, origin: str, source: str, file_sha: str, skipped: list[str]
) -> list[Any]:
    from chimera.governance.hooks import POST_TOOL, PRE_TOOL, Hook

    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
        skipped.append(f"{event}: a matcher entry without a hooks list")
        return []
    try:
        matcher = compile_matcher(group.get("matcher"))
    except re.error as exc:
        skipped.append(f"{event}: matcher is not a regular expression ({exc})")
        return []
    internal = {PRE_TOOL_USE: PRE_TOOL, POST_TOOL_USE: POST_TOOL}.get(event, event)
    hooks: list[Hook] = []
    for entry in group["hooks"]:
        if not isinstance(entry, dict) or entry.get("type", "command") != "command":
            kind = entry.get("type") if isinstance(entry, dict) else type(entry).__name__
            skipped.append(f"{event}: a {kind!r} hook (only command hooks run here)")
            continue
        command = str(entry.get("command") or "").strip()
        if not command:
            skipped.append(f"{event}: a command hook without a command")
            continue
        timeout = _timeout(entry.get("timeout"))
        on_failure = str(entry.get("onFailure") or "continue").strip().lower()
        if on_failure not in ("block", "continue"):
            skipped.append(f"{event}: onFailure {on_failure!r} read as continue")
            on_failure = "continue"
        hooks.append(Hook(
            id=f"{origin}:{event}:{len(hooks) + 1}:{hashlib.sha256(command.encode()).hexdigest()[:6]}",
            event=internal, tools=None, pattern=None, command=command, timeout=timeout,
            on_error="deny" if on_failure == "block" else "ignore",
            origin=origin, protocol=CLAUDE, matcher=matcher, claude_event=event, source=source,
            source_sha256=file_sha,
        ))
    return hooks


def _timeout(raw: Any) -> int:
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return max(1, min(value, MAX_TIMEOUT))


def discover(
    workspace: Path, *, repo: bool, user: bool, user_home: Path | None = None,
    repo_trust: str = TRUSTED,
) -> tuple[list[Any], list[HookSource]]:
    """Every Claude-format hook of the origins that are switched on, plus one ``HookSource`` per
    file (also for the ones that are off or missing, so the screen can say so)."""
    hooks: list[Any] = []
    sources: list[HookSource] = []
    seen: set[tuple[str, str, str]] = set()
    for origin, path in settings_files(workspace, user_home):
        enabled = repo if origin == ORIGIN_REPO else user
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            sources.append(HookSource(origin, str(path), exists=False, enabled=enabled))
            continue
        except OSError as exc:
            sources.append(HookSource(origin, str(path), exists=True, enabled=enabled,
                                      error=f"could not read: {exc}"))
            continue
        sha = hashlib.sha256(raw).hexdigest()
        trust = repo_trust if origin == ORIGIN_REPO else ""
        if not enabled:
            sources.append(HookSource(origin, str(path), exists=True, enabled=False, sha256=sha,
                                      trust=trust))
            continue
        if origin == ORIGIN_REPO and trust != TRUSTED:
            # Listed, not parsed: nothing a repository wrote is read into a hook until confirmed.
            sources.append(HookSource(origin, str(path), exists=True, sha256=sha, trust=trust))
            continue
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            sources.append(HookSource(origin, str(path), exists=True, sha256=sha,
                                      error=f"not valid JSON: {exc}"))
            continue
        found, skipped = parse_claude_settings(data, origin=origin, source=str(path), file_sha=sha)
        kept = []
        for hook in found:
            key = (hook.claude_event, hook.matcher.pattern if hook.matcher else "", hook.command)
            if key not in seen:  # Claude Code runs an identical command once
                seen.add(key)
                kept.append(hook)
        hooks.extend(kept)
        sources.append(HookSource(origin, str(path), exists=True, sha256=sha, hooks=len(kept),
                                  skipped=tuple(skipped), trust=trust))
    return hooks, sources


# --- what a hook may answer ---------------------------------------------------------------------

NONE, ANNOTATE, ASK, DENY = "none", "annotate", "ask", "deny"
_HONOURED: dict[str, frozenset[str]] = {
    PRE_TOOL_USE: frozenset({DENY, ASK, ANNOTATE}),
    POST_TOOL_USE: frozenset({DENY, ANNOTATE}),
    USER_PROMPT: frozenset({DENY, ANNOTATE}),
}
_ANNOTATE_ONLY = frozenset({ANNOTATE})
#: Keys of a JSON answer this module reads. Anything else is listed as refused on the receipt.
_KNOWN_KEYS = frozenset(
    {"decision", "reason", "continue", "stopReason", "suppressOutput", "systemMessage",
     "hookSpecificOutput"}
)
_SPECIFIC_KEYS = frozenset(
    {"hookEventName", "permissionDecision", "permissionDecisionReason", "updatedInput",
     "additionalContext"}
)
_DENY_WORDS = {"deny": DENY, "block": DENY, "ask": ASK}
_NO_OBJECTION = frozenset({"allow", "approve"})


@dataclass
class Answer:
    decision: str = NONE
    reason: str = ""
    notes: list[str] = field(default_factory=list)
    requested: str = ""
    refused: list[str] = field(default_factory=list)
    error: str = ""
    updated_input: dict[str, Any] | None = None


def read_answer(event: str, exit_code: int, stdout: str, stderr: str) -> Answer:
    """What a finished hook said, by Claude Code's protocol, with every widening reading removed.

    Exit 2 blocks, with stderr as the reason. Exit 0 may print one JSON object. Any other exit code
    is an error (the caller applies ``onFailure``). ``allow`` is "no objection", never a release.
    """
    honoured = _HONOURED.get(event, _ANNOTATE_ONLY)
    if exit_code == 2:
        said = stderr.strip()[:_MAX_TEXT] or "the hook blocked this (exit 2)"
        if DENY in honoured:
            return Answer(DENY, reason=said, requested="exit:2")
        return Answer(ANNOTATE, notes=[said], requested="exit:2")
    if exit_code != 0:
        tail = stderr.strip().splitlines()[-1][:200] if stderr.strip() else ""
        return Answer(error=f"exited {exit_code}" + (f": {tail}" if tail else ""))
    text = stdout.strip()
    if not text:
        return Answer()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Claude Code adds plain stdout to the model's context on these two events, and nowhere else.
        if event in (USER_PROMPT, SESSION_START):
            return Answer(ANNOTATE, notes=[text[:_MAX_TEXT]])
        return Answer()
    if not isinstance(obj, dict):
        return Answer(error="printed JSON that is not an object")
    return _read_object(obj, honoured)


def _read_object(obj: dict[str, Any], honoured: frozenset[str]) -> Answer:
    answer = Answer()
    specific = obj.get("hookSpecificOutput")
    specific = specific if isinstance(specific, dict) else {}
    answer.refused = sorted(
        [f"key:{k}" for k in set(obj) - _KNOWN_KEYS]
        + [f"key:hookSpecificOutput.{k}" for k in set(specific) - _SPECIFIC_KEYS]
    )
    requested = str(specific.get("permissionDecision") or obj.get("decision") or "").strip().lower()
    reason = str(
        specific.get("permissionDecisionReason") or obj.get("reason") or obj.get("stopReason") or ""
    )[:_MAX_TEXT]
    answer.requested = requested
    decision = NONE
    if requested in _NO_OBJECTION:
        decision = NONE  # "no objection": nothing is applied, and nothing is released either
    elif requested in _DENY_WORDS:
        decision = _DENY_WORDS[requested]
    elif requested:
        answer.refused.append(f"decision:{requested}")
    if obj.get("continue") is False and decision != DENY:
        decision, reason = DENY, reason or "the hook stopped processing (continue: false)"
        answer.requested = answer.requested or "continue:false"
    if decision == ASK and ASK not in honoured:
        decision = DENY if DENY in honoured else ANNOTATE
    if decision == DENY and DENY not in honoured:
        decision = ANNOTATE
    answer.decision = decision
    if decision in (DENY, ASK):
        answer.reason = reason
    elif reason and decision == ANNOTATE:
        answer.notes.append(reason)
    for extra in (specific.get("additionalContext"), obj.get("systemMessage")):
        if isinstance(extra, str) and extra.strip():
            answer.notes.append(extra.strip()[:_MAX_TEXT])
    if answer.notes and answer.decision == NONE:
        answer.decision = ANNOTATE
    updated = specific.get("updatedInput")
    if isinstance(updated, dict):
        answer.updated_input = updated
    return answer


#: ``tool -> argument names`` whose list is an INCLUSION list (what the call may touch), so that
#: cutting it down narrows the call. EMPTY by default, and meant to stay so until an argument has been
#: shown to be one: a list that EXCLUDES (``scrape.exclude``) or that GRANTS (``subagent.tools``)
#: widens the call when it shrinks, and "a list got shorter" cannot tell the two apart.
NARROWING_ALLOWLIST: dict[str, frozenset[str]] = {}


def narrowed(tool: str, original: dict[str, Any], updated: dict[str, Any]) -> dict[str, Any] | None:
    """``updated`` when it is a strict narrowing of ``original``, else ``None``.

    The only change accepted is an inclusion list, one NAMED in :data:`NARROWING_ALLOWLIST` for this
    tool, cut down to some of its own items in their order and never to nothing, with every other key
    identical and none added. By default no (tool, key) pair is listed, so nothing is accepted: a
    shorter string is not narrower (``rm -rf /`` is short), a smaller number is not (``offset``), a
    removed key can change a default, and a list's direction depends on what it means. In doubt the
    caller ignores the update and says so.
    """
    allowed = NARROWING_ALLOWLIST.get(tool, frozenset())
    if set(updated) != set(original):
        return None
    changed = False
    for key, was in original.items():
        now = updated[key]
        if now == was:
            continue
        if key not in allowed:
            return None
        if not (isinstance(was, list) and isinstance(now, list) and 0 < len(now) < len(was)):
            return None
        remaining = iter(was)
        if not all(any(item == other for other in remaining) for item in now):
            return None
        changed = True
    return dict(updated) if changed else None


# --- running them -------------------------------------------------------------------------------


def tool_input(args: dict[str, Any]) -> dict[str, Any]:
    """The call's arguments as Claude Code names them: ``path`` is also ``file_path``. Document
    bodies are replaced by their size (A6), as for the owner's hooks."""
    mapped = dict(elide_values(args))
    if "path" in mapped and "file_path" not in mapped:
        mapped["file_path"] = mapped["path"]
    return mapped


def _with_stdin(command: str, project: str) -> str:
    """The command with ``event.json`` on its stdin, as Claude Code feeds a hook its JSON."""
    if os.name == "nt":
        # `set "VAR=value"` then the command GROUPED, so the redirect covers a `a && b` chain and not
        # only its last word; the value is quoted for cmd, whose own quotes cannot appear in a path.
        safe = project.replace('"', "")
        # No newline before `)`: cmd.exe ends the command line at one.
        return f'set "CLAUDE_PROJECT_DIR={safe}" && ( {command} ) < event.json'
    return f"CLAUDE_PROJECT_DIR={shlex.quote(project)}; export CLAUDE_PROJECT_DIR; ( {command}\n) < event.json"


@dataclass
class _Exec:
    hook: Any
    ran: bool = False
    sandbox: str = ""
    isolated: bool | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    duration_ms: float = 0.0


def may_run_on_host(runner: Any, hook: Any) -> bool:
    """Whether this hook may run where no sandbox isolates. A repository's NEVER, whatever the owner
    set; the user's own only under their own switch; the owner's file keeps ``CHIMERA_HOOKS_HOST_EXEC``."""
    if hook.origin == ORIGIN_REPO:
        return False
    if hook.origin == ORIGIN_USER:
        return bool(getattr(runner, "user_host_exec", False))
    return bool(runner.host_exec)


def _breaker_key(hook: Any) -> tuple[str, str]:
    return (hook.source_sha256, hook.id)


def skipped_count(hook: Any) -> int:
    """How many times this hook was skipped without running, since the process started."""
    with _BREAKER_LOCK:
        return _SKIPPED.get(_breaker_key(hook), 0)


def _execute(runner: Any, hook: Any, payload: dict[str, Any], deadline: float = 0.0) -> _Exec:
    done = _attempt(runner, hook, payload, deadline)
    if not done.ran:
        with _BREAKER_LOCK:
            _SKIPPED[_breaker_key(hook)] = _SKIPPED.get(_breaker_key(hook), 0) + 1
    return done


def _attempt(runner: Any, hook: Any, payload: dict[str, Any], deadline: float = 0.0) -> _Exec:
    from chimera.governance.hooks import _sandbox_name
    from chimera.sandbox.confirm import sandbox_is_isolated

    done = _Exec(hook)
    with _BREAKER_LOCK:
        opened = _BREAKER.get(_breaker_key(hook), 0) >= BREAKER_TIMEOUTS
    if opened:
        done.error = f"skipped: it timed out {BREAKER_TIMEOUTS} times (circuit open)"
        return done
    remaining = hook.timeout
    if deadline:
        remaining = min(hook.timeout, int(deadline - time.monotonic()))
        if remaining < 1:
            done.error = "skipped: the time budget for this event is spent"
            return done
    try:
        sandbox = runner._sandbox()  # noqa: SLF001 — the runner owns the factory
        done.isolated = sandbox_is_isolated(sandbox)
        done.sandbox = _sandbox_name(sandbox)
    except Exception as exc:  # noqa: BLE001 — a hook that cannot start is a failed hook
        done.error = f"could not start a sandbox: {type(exc).__name__}: {exc}"
        return done
    if not done.isolated and not may_run_on_host(runner, hook):
        if hook.origin == ORIGIN_REPO:
            done.error = "a repository's hooks never run on the host and no sandbox isolates here"
        elif hook.origin == ORIGIN_USER:
            done.error = "no isolating sandbox, and CHIMERA_CLAUDE_USER_HOOKS_HOST_EXEC is off"
        else:
            done.error = "no isolating sandbox, and CHIMERA_HOOKS_HOST_EXEC is off"
        return done
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="chimera-hook-") as tmp:
        Path(tmp, "event.json").write_text(json.dumps(payload, default=str), encoding="utf-8")
        try:
            ran = sandbox.run(
                _with_stdin(hook.command, str(payload.get("cwd") or "")),
                timeout=remaining, cwd=Path(tmp),
            )
            done.ran = True
            done.exit_code = int(ran.exit_code)
            done.stdout, done.stderr = ran.stdout or "", ran.stderr or ""
            if ran.timed_out:
                done.error = f"timed out after {remaining}s"
                with _BREAKER_LOCK:
                    _BREAKER[_breaker_key(hook)] = _BREAKER.get(_breaker_key(hook), 0) + 1
        except Exception as exc:  # noqa: BLE001
            done.error = f"could not run: {type(exc).__name__}: {exc}"
    done.duration_ms = (time.perf_counter() - started) * 1000
    return done


def run_batch(
    runner: Any, hooks: list[Any], event: str, outcome: Any, payload: dict[str, Any],
    *, tool: str = "",
) -> None:
    """Run the matching Claude-format hooks of one event, at most :data:`MAX_PARALLEL` at once, then
    fold their answers into ``outcome`` in file order (so the result never depends on timing)."""
    from chimera.governance.hooks import _Said

    claude_event = hooks[0].claude_event
    if len(hooks) > MAX_HOOKS_PER_EVENT:
        _log.warning("%d hooks match %s; only the first %d run", len(hooks), claude_event,
                     MAX_HOOKS_PER_EVENT)
        hooks = hooks[:MAX_HOOKS_PER_EVENT]
    body = {"session_id": runner.session_id, "transcript_path": "",
            "hook_event_name": claude_event, **payload}
    deadline = time.monotonic() + EVENT_BUDGET_S
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(hooks))) as pool:
        execs = list(pool.map(lambda h: _execute(runner, h, body, deadline), hooks))
    for done in execs:
        hook = done.hook
        answer = Answer()
        error = done.error
        if done.ran and not error:
            answer = read_answer(claude_event, done.exit_code or 0, done.stdout, done.stderr)
            error = answer.error
        applied = answer.decision
        if error:
            blocks = hook.on_error == "deny" and claude_event in _HONOURED and DENY in _HONOURED[claude_event]
            applied = DENY if blocks else NONE
            answer = Answer(applied, reason=f"the hook failed ({error})" if blocks else "")
            outcome.take(applied, hook.id, hook)
            if blocks:
                # The failure sentence carries the tail of the command's stderr: text a command
                # computed, so it is fenced and taints the run like any other thing a hook says.
                outcome.reasons.append(_Said(hook.id, answer.reason, done.ran))
        else:
            outcome.take(answer.decision, hook.id, hook)
            if answer.reason and answer.decision in (DENY, ASK):
                outcome.reasons.append(_Said(hook.id, answer.reason, True))
            for note in answer.notes:
                outcome.notes.append(_Said(hook.id, note, True))
            if answer.updated_input is not None:
                outcome.updated_input = answer.updated_input
                outcome.updated_by = hook.id
        runner.receipt_for(
            hook, event, tool, done, requested=answer.requested, applied=applied,
            refused=answer.refused, error=error,
        )


class ClaudeLifecycle:
    """The non-tool events of Claude Code, fired from the agent loop.

    Every method returns the composed :class:`HookOutcome` (never raises) and does nothing when no
    hook is registered for the event, so a run with no ``.claude`` hooks pays one dict lookup.
    """

    def __init__(self, runner: Any, *, subagent: bool = False) -> None:
        self.runner = runner
        self.subagent = subagent

    def as_subagent(self) -> ClaudeLifecycle:
        """The same hooks for a nested run: its task is not a person's prompt and it opens no
        session, and it ends with ``SubagentStop`` rather than ``Stop``."""
        return ClaudeLifecycle(self.runner, subagent=True)

    def spoken(self, outcome: Any) -> str:
        """What the hooks of an event said, as the model reads it: sanitised, fenced, tainting."""
        from chimera.governance.hooks import _spoken

        return _spoken(outcome.notes, self.runner.taint)

    def spoken_reasons(self, outcome: Any) -> str:
        """The reasons of a deny/ask as data: sanitised, fenced, and tainting when a command said them."""
        from chimera.governance.hooks import _spoken

        return _spoken(outcome.reasons, self.runner.taint)

    def plain(self, outcome: Any) -> list[str]:
        """The same notes for the PERSON (a notification), sanitised but not for the model."""
        from chimera.governance.sanitize import sanitize_untrusted

        return [f"[hook {n.hook}] {sanitize_untrusted(n.text)}" for n in outcome.notes]

    def has(self, claude_event: str) -> bool:
        return any(h.protocol == CLAUDE and h.claude_event == claude_event
                   for h in self.runner.config.hooks)

    def fire(self, claude_event: str, *, value: str = "", **payload: Any) -> Any:
        from chimera.governance.hooks import HookOutcome

        outcome = HookOutcome()
        picked = [h for h in self.runner.config.hooks
                  if h.protocol == CLAUDE and h.claude_event == claude_event
                  and (h.matcher is None or h.matcher.search(value or ""))]
        if not picked:
            return outcome
        base = {"cwd": str(self.runner.workspace)}
        try:
            run_batch(self.runner, picked, claude_event, outcome, {**base, **payload})
        except Exception:  # noqa: BLE001 — a hook machinery failure must not take the turn down
            _log.warning("lifecycle hooks for %s failed", claude_event, exc_info=True)
        return outcome

    def user_prompt(self, prompt: str) -> Any:
        if self.subagent:
            return self.fire("")
        return self.fire(USER_PROMPT, prompt=prompt[:_MAX_PROMPT])

    def session_start(self, source: str = "startup") -> Any:
        if self.subagent:
            return self.fire("")
        return self.fire(SESSION_START, value=source, source=source)

    def stop(self, *, reason: str = "") -> Any:
        return self.fire(SUBAGENT_STOP if self.subagent else STOP, stop_hook_active=False,
                         stopped_reason=reason)

    def notification(self, code: str, text: str) -> Any:
        return self.fire(NOTIFICATION, value=code, message=text[:_MAX_TEXT], notification_type=code)

    def pre_compact(self, trigger: str = "auto") -> Any:
        return self.fire(PRE_COMPACT, value=trigger, trigger=trigger)


def new_session_id() -> str:
    return uuid.uuid4().hex


def _mask(command: str, limit: int = 80) -> str:
    """A command as the screen may show it: secrets masked, cut at ``limit`` characters."""
    from chimera.core.redact import redact

    text = redact(command.replace("\n", " ").strip())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def isolation_hint(settings: Any) -> bool | None:
    """Whether a sandbox that isolates exists, WITHOUT probing Docker: ``None`` when the answer would
    need ``docker version`` (the configured backend is Docker), so a request thread never waits on a
    daemon. A screen reads ``None`` as "not known here", never as "will not run"."""
    choice = str(getattr(settings, "sandbox", "auto") or "auto").lower()
    if choice == "docker":
        return None
    if choice in ("auto", "os"):
        try:
            from chimera.sandbox.os_sandbox import os_sandbox_available

            return bool(os_sandbox_available())
        except Exception:  # noqa: BLE001
            return False
    return False


def describe(settings: Any, workspace: Path | None = None) -> dict[str, Any]:
    """What the Settings screen lists: every settings file looked at and every hook loaded, with its
    origin, its trust state (repo) and whether it can run on this machine per origin.

    Read-only; nothing here starts a hook, and nothing here talks to Docker. A command is masked and
    truncated: it may name a path or carry a token, and the screen needs to recognise it, not copy it."""
    ws = Path(workspace) if workspace else Path.cwd()
    home = Path(settings.home)
    hooks, sources = discover(
        ws,
        repo=bool(getattr(settings, "claude_hooks_repo", True)),
        user=bool(getattr(settings, "claude_hooks_user", True)),
        repo_trust=trust_state(home, ws),
    )
    isolated = isolation_hint(settings)
    pending: list[Any] = []
    if any(x.origin == ORIGIN_REPO and x.enabled and x.trust != TRUSTED and x.exists for x in sources):
        # For DISPLAY only: parsed so the owner sees what they are about to confirm. Not loaded, and
        # not trusted; the button sends the fingerprint shown beside these.
        pending, _ = discover(ws, repo=True, user=False, repo_trust=TRUSTED)

    def runnable(origin: str) -> bool | None:
        if origin == ORIGIN_USER and bool(getattr(settings, "claude_user_hooks_host_exec", False)):
            return True
        return isolated

    def shown(h: Any) -> dict[str, Any]:
        return {"origin": h.origin, "event": h.claude_event,
                "matcher": h.matcher.pattern if h.matcher else "*",
                "command": _mask(h.command), "timeout": h.timeout,
                "on_failure": "block" if h.on_error == "deny" else "continue", "source": h.source,
                # Per hook, so the screen can mark the one that will not run here and say how often
                # it was skipped; None when the answer would need Docker probed.
                "runs_here": runnable(h.origin), "skipped": skipped_count(h)}

    return {
        "workspace": str(ws),
        "runnable_repo": runnable(ORIGIN_REPO),
        "runnable_user": runnable(ORIGIN_USER),
        "sources": [
            {"origin": s.origin, "path": s.path, "exists": s.exists, "enabled": s.enabled,
             "hooks": s.hooks, "error": s.error, "skipped": list(s.skipped), "trust": s.trust}
            for s in sources
        ],
        "hooks": [shown(h) for h in hooks],
        "pending": [shown(h) for h in pending],
        "fingerprint": repo_fingerprint(ws) if pending else "",
    }
