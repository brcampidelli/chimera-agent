"""`/api/preview` — the dev servers of the open project, started from its ``launch.json``.

The panel on the Code screen reads and drives them here. The doors are the terminal's
(`terminal_api`), for the same reason: starting a dev server is running a command on this computer.

* **Only the desktop app's own screen.** The bearer token is a file the agent's shell can read; a paired
  browser tab, a paired device, the Claude bridge, a bot and a guest link are refused, and the routes are
  absent from the bridge's table.
* **Commands off means no server.** A folder whose commands the owner has not granted, a deployment that
  denies ``run_shell`` and a container sandbox answer 403 with the reason, exactly as a terminal would.
* **A name, never a command.** ``start`` takes the NAME of an entry in the folder's own recipe; what runs
  is what the file says, validated (`launch_config`), under the same scrubbed environment as every child.
* **Stopping and quitting kill the tree.** `DevServerManager.stop` and the shutdown hook below.

The log is the last 300 lines of the server's output, as the server printed them: not redacted by this
route, which is why the route is the owner's screen only.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, params
from pydantic import BaseModel, Field

from chimera.api.terminal_api import (
    COMMANDS_OFF_DETAIL,
    SANDBOX_DETAIL,
    folder_refusal,
    owner_refusal,
)
from chimera.config import Settings
from chimera.core.dev_servers import DevServerError, DevServerManager, manager, state_payload
from chimera.core.launch_config import LaunchConfig, ServerConfig, load_launch_config, resolve_cwd
from chimera.telemetry import get_logger
from chimera.tools.browser_reach import chimera_ports
from chimera.tools.preview import start_command
from chimera.tools.workspace import audit_refusal, queue_refusal

_log = get_logger("api.preview")

__all__ = ["COMMANDS_OFF_DETAIL", "register_preview_api"]


class PreviewServerOut(BaseModel):
    name: str
    state: str
    port: int
    kind: str
    pid: int | None = None
    url: str | None = None
    exit_code: int | None = None
    #: The whole command line this click would run (env as prefixes, executable, arguments).
    command: str = ""
    #: The folder it runs in, relative to the project ("." for the root).
    cwd: str = "."
    #: Set when the job registry's maximum runtime ended it: the limit, in hours.
    limit_hours: int | None = None


class PreviewServersOut(BaseModel):
    found: bool
    source: str = ""
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    #: The project's own ``autoVerify``.
    auto_verify: bool = True
    #: The owner's Settings switch (CHIMERA_PREVIEW_AUTO_VERIFY). A check runs only when both are on.
    auto_verify_setting: bool = True
    can_start: bool = False
    cannot_start_reason: str = ""
    #: Recipe or script files that changed since the newest agent turn in this folder began.
    config_changed: list[str] = Field(default_factory=list)
    servers: list[PreviewServerOut] = Field(default_factory=list)


class PreviewNameIn(BaseModel):
    workspace: str | None = None
    name: str = Field(min_length=1, max_length=60)


class PreviewLogOut(BaseModel):
    lines: list[str]


class PreviewOkOut(BaseModel):
    ok: bool
    message: str = ""


def _full_command(entry: ServerConfig, port: int) -> str:
    """What a click would run, env and all: the line the policy kernel judges and the panel shows."""
    try:
        return start_command(entry, port)
    except DevServerError:
        return ""


def _kernel_refusal(line: str) -> str:
    """The reason the policy kernel BLOCKS ``line`` as a `run_shell` command, or "".

    The same rules (`RuleSet`, the lexical denylist of `chimera.governance.policy`) and the same rendering
    of the action that `GovernedTool` uses for the agent's commands. A hard block stays blocked when the
    owner clicks: the panel is the owner's, but a recipe is project content and the rules exist for it.
    Review and warn verdicts are not refusals here: the click is the owner's decision, and the panel has
    already shown the whole command.
    """
    from chimera.governance.governed_tool import render_action
    from chimera.governance.policy import Decision, RuleSet

    action, document = render_action("run_shell", {"command": line})
    verdict = RuleSet().evaluate(action, document=document)
    if verdict is not None and verdict.decision == Decision.BLOCK:
        return f"blocked by the safety rules ({verdict.reason}): {line[:200]}"
    return ""


def _server_rows(config: LaunchConfig | None, mgr: DevServerManager, root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in config.configurations if config else []:
        info = state_payload(mgr.get(root, entry.name), entry)
        port = int(info["port"])
        cwd = resolve_cwd(root, entry.cwd)
        rows.append({
            "name": entry.name, "state": info["state"], "port": port, "kind": info["kind"],
            "pid": info["pid"], "url": info["url"], "exit_code": info["exit_code"],
            "command": "" if entry.attach_only else _full_command(entry, port),
            "cwd": "." if cwd is None or cwd == root.resolve() else str(cwd.relative_to(root.resolve())),
            "limit_hours": info["limit_hours"],
        })
    return rows


def register_preview_api(
    app: FastAPI,
    guard: params.Depends,
    resolve_workspace: Callable[[str | None], Path],
    settings: Settings,
    *,
    live_settings: Callable[[], Settings] | None = None,
) -> None:
    live = live_settings or (lambda: settings)

    def owner_only(request: Request) -> None:
        why = owner_refusal(request)
        if why:
            raise HTTPException(status_code=403, detail=why)

    app.router.add_event_handler("shutdown", manager().close_all)

    @app.get("/api/preview/servers", dependencies=[guard], response_model=PreviewServersOut)
    def preview_servers(request: Request, workspace: str | None = None) -> dict[str, Any]:
        owner_only(request)
        root = resolve_workspace(workspace)
        found = load_launch_config(root)
        why = folder_refusal(live(), root)
        config = found.config
        return {
            "found": found.found,
            "source": "" if found.source is None else str(found.source.relative_to(root)).replace("\\", "/"),
            "errors": list(found.errors),
            "warnings": list(found.warnings),
            "auto_verify": True if config is None else config.autoVerify,
            "auto_verify_setting": bool(live().preview_auto_verify),
            "can_start": config is not None and not why,
            "cannot_start_reason": why,
            "config_changed": manager().changed_since_turn(root),
            "servers": _server_rows(config, manager(), root),
        }

    def entry_of(root: Path, name: str) -> ServerConfig:
        found = load_launch_config(root)
        if found.config is None:
            raise HTTPException(status_code=409, detail="; ".join(found.errors) or "this folder has no launch.json")
        entry = found.config.get(name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"no server named {name!r} in launch.json")
        return entry

    @app.post("/api/preview/servers/start", dependencies=[guard], response_model=PreviewOkOut)
    def preview_start(body: PreviewNameIn, request: Request) -> dict[str, Any]:
        owner_only(request)
        root = resolve_workspace(body.workspace)
        why = folder_refusal(live(), root)
        if why:
            raise HTTPException(status_code=403, detail=why)
        entry = entry_of(root, body.name)
        if not entry.attach_only:
            # The same fences `run_shell` puts in front of a command, for the command this click starts:
            # the approval queue and audit log are not reachable from it, and only a plain host sandbox
            # (not a container, not a kernel sandbox that wraps the command) runs a server this way.
            from chimera.sandbox import get_sandbox
            from chimera.sandbox.local import LocalSandbox

            if type(get_sandbox(live())) is not LocalSandbox:
                raise HTTPException(status_code=403, detail=SANDBOX_DETAIL)
            line = _full_command(entry, entry.port) or entry.command_line(entry.port)
            refused = (
                _kernel_refusal(line)
                or queue_refusal("run_shell", line, root)
                or audit_refusal("run_shell", line, root)
            )
            if refused:
                raise HTTPException(status_code=403, detail=refused)
        try:
            server = manager().start(
                root, entry, started_by="owner", forbidden_ports=chimera_ports(Path(live().home)),
            )
        except DevServerError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "message": f"port {server.port}"}

    @app.post("/api/preview/servers/stop", dependencies=[guard], response_model=PreviewOkOut)
    def preview_stop(body: PreviewNameIn, request: Request) -> dict[str, Any]:
        owner_only(request)
        root = resolve_workspace(body.workspace)
        stopped = manager().stop(root, body.name)
        return {"ok": True, "message": "stopped" if stopped else "was not running"}

    @app.get("/api/preview/servers/log", dependencies=[guard], response_model=PreviewLogOut)
    def preview_log(request: Request, name: str, workspace: str | None = None, lines: int = 80) -> dict[str, Any]:
        owner_only(request)
        root = resolve_workspace(workspace)
        return {"lines": manager().tail(root, name, lines)}
