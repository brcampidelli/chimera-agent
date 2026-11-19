"""What a share link's guest may reach: the one conversation, and nothing else of the owner's.

A guest's turn is built by the same machinery as the owner's (`chimera.api.code_api`), so every
source the owner's turn reads is a candidate for leaking to whoever holds the link. Rather than an
`if guest` beside each source, the guest's reach is decided here, in one place, and the builder asks
for it once:

* tools that read the owner's other records or accounts are not mounted at all (`WITHHELD_TOOLS`);
* the tools that remain and see more than the conversation are confined to it
  (`confine_to_conversation`): background jobs are visible by the conversation that started
  them, not by the folder they ran in, and the browser keeps no sign-in (always ephemeral);
* nothing reads outside the conversation's folder, whatever the approval mode: the guest's file tools
  get no "ask outside" approver, and `CHIMERA_APPROVAL_MODE=allow` is read as `ask` for the guest;
* guest turns exclude `run_shell`, `execute_code`, the verifier, setup, and hooks from all origins
  (owner `chimera-hooks.json`, `~/.claude/settings.json`, and project hooks);
* the prompt sources that belong to the owner (`agent.json`, the global AGENTS.md, the owner's skill
  list, learned lessons) are withheld by the builder through ``AgentConfig.guest``.

Deliberately NOT here: the project's own instructions (AGENTS.md / CLAUDE.md of the repository) and the
folder's files. The guest works in that project; that is what a share link is for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: Tools a guest's turn is never given.
#:
#: * ``recall_history`` searches the owner's OTHER conversations (and, with ``everywhere``, other
#:   projects'): the guest's own conversation is already in front of the model.
#: * ``read_email`` / ``calendar_events`` read the owner's mailbox and calendar with the owner's keys.
#: * ``job_status`` / ``job_cancel`` list and read background jobs by the folder, and a job's command,
#:   directory and output are whatever the owner's turn ran: the filter by conversation cannot tell
#:   the guest's job from the owner's job in the SAME shared conversation. A guest has no shell and so
#:   starts no job of its own; there is nothing of its own to look up.
#: * ``skill_view`` reads the owner's skill bundles, plugins' included.
#: * ``run_shell`` / ``execute_code`` run on the owner's machine, where ``cat``/``type`` reads any
#:   path (the owner's ``agent.json``, ``.env``, other projects): no approval mode and no read-only
#:   classifier can make a shell "read the workspace only", so the guest has no shell at all. The
#:   cost is real (a guest cannot run the project's tests) and is the owner's to lift later with a
#:   sandbox that mounts the workspace alone.
WITHHELD_TOOLS: frozenset[str] = frozenset(
    {
        "recall_history", "read_email", "calendar_events", "skill_view", "job_status", "job_cancel",
        "run_shell", "execute_code",
        # Each of these runs something the guest's own edits can change: a dev server starts the
        # project's recipe, and a pull request runs git (hooks) and gh with the owner's credentials.
        "preview", "open_pull_request",
        # yt-dlp follows the URLs a page names (a <video src> on 127.0.0.1) and only the first was
        # checked against the internal-address guard; the response lands in the folder.
        "download_media",
        # The decoder (ffmpeg) opens what a playlist or concat manifest names, and a guest can write one.
        "transcribe_audio",
    }
)


#: What a guest's turn says where a verifier's output would be: the check did not run, and why.
GUEST_UNVERIFIED_OUTPUT = (
    "not verified: a guest's turn does not run the project's verifier or any other command, because "
    "the guest can have edited what it would run"
)


def guest_runs_nothing(author: str | None, req: Any) -> bool:
    """Whether this turn (a share link's, told by its author and not a linked chat) runs no command
    of its own at the end: no verifier, no setup. One question, asked in the places that would run one."""
    return bool(author) and not getattr(req, "channel_origin", None)


def guest_governance_settings(settings: Any) -> Any:
    """``settings`` as the governance layer (policy, hooks) should read them for a guest's turn.

    Identical except that EVERY hook source is off: ``CHIMERA_HOME/chimera-hooks.json``, the user-level
    ``~/.claude/settings.json`` and the project's ``.claude/settings.json``. The first two are the
    owner's commands; the project's are approved by fingerprint once, and the guest can then rewrite
    the script that fingerprint names (the post hook of the same write runs the new bytes). This
    reverses the first round, which kept the project's hooks.

    The turn's other automatic runs are off for the same reason (the principle: nothing runs that
    the guest can have edited): the end-of-turn verifier and the project's setup command are skipped
    in `code_api` through ``guest_runs_nothing``, the formatter by ``confine_to_conversation``, and the
    dev server and pull request tools by ``WITHHELD_TOOLS``.
    """
    return settings.model_copy(
        update={"hooks": False, "claude_hooks_user": False, "claude_hooks_repo": False}
    )


def denied_roots(settings: Any) -> tuple[Path, ...]:
    """The folders and files private to the owner that no read, listing or search of a guest's turn
    may reach, even when they sit INSIDE the workspace.

    ``CHIMERA_HOME`` is the legacy default's ``.chimera/`` under the project for some installs, and
    everything private lives under it (``agent.json``, memory, history, logs, approvals, credentials,
    the MCP sign-in store, the home's ``.env``). The others are the owner's deployment policy, the
    user-level Claude settings folder and the desktop shell's own files, wherever they are configured.
    The workspace jail alone says "inside, allowed".
    """
    import os

    roots: list[Path] = [Path(settings.home).expanduser()]
    try:
        from chimera.core.own_files import own_env_files

        roots.extend(own_env_files())
    except Exception:  # the identity check (`private_to_owner`) still covers them
        pass
    try:
        from chimera.deployment_policy import policy_path

        roots.append(Path(policy_path(settings)))
    except Exception:  # a policy that cannot be located is not a reason to drop the others
        pass
    try:
        from chimera.core.agents_md import claude_config_dir

        roots.append(claude_config_dir())
    except Exception:
        pass
    for variable in ("CHIMERA_SHELL_PREFS", "CHIMERA_SHELL_STATE"):
        value = os.environ.get(variable, "").strip()
        if value:
            roots.append(Path(value))
    return tuple(roots)


def confine_to_conversation(registry: Any, session_id: str, *, settings: Any = None) -> None:
    """Confine the tools that stay to the one conversation.

    ``job_status`` / ``job_cancel`` (and the job-limit refusal of a shell, should one be mounted) see
    only the jobs ``session_id`` started; an empty id confines to nothing. The browser is made
    ephemeral whatever ``CHIMERA_BROWSER_COOKIE_RETENTION`` says: ``shared`` hands the owner's
    sign-ins to the guest, and ``conversation`` hands over the ones made in this very conversation.
    Applied to the raw tools, before any wrapper copies the registry, as the plugin scope is.
    """
    # Every tool bound to a folder treats the owner's private folders as absent, and Chimera's own
    # `.env` is hidden whatever the owner's setting for the agent's reads says.
    private = denied_roots(settings) if settings is not None else ()
    for tool in registry.tools():
        if hasattr(tool, "workspace"):
            tool.denied_roots = private
            tool.hide_own_env = True
    confined = session_id or "-no-conversation"
    for name in ("job_status", "job_cancel", "run_shell"):
        if name in registry:
            tool = registry.get(name)
            if hasattr(tool, "only_session"):
                tool.only_session = confined
    # Nothing runs after an edit: the formatter reads configuration (`.prettierrc.cjs`) that the same
    # turn can write.
    for tool in registry.tools():
        if hasattr(tool, "formats"):
            tool.formats = False
    if "browser" in registry:
        browser = registry.get("browser")
        if hasattr(browser, "cookie_retention"):
            browser.cookie_retention = "ephemeral"
        # The owner's declared local ports and site list (`CHIMERA_BROWSER_LOCAL_PORTS`, `_SITES`) are
        # the owner's reach: a guest's browser gets the public-internet floor and nothing declared.
        for attribute in ("reach", "_turn_reach", "_turn_ports"):
            if hasattr(browser, attribute):
                setattr(browser, attribute, None)
