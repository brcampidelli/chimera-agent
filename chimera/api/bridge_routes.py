"""What the desktop bridge may reach, as data: one table the server enforces and the MCP tools list.

The bridge (`chimera/api/desktop_bridge.py`) forwards a call to the app's own routes in-process. It
never forwards a URL the caller wrote: the caller names a ROUTE ID from this table, and the server
builds the path from the template here. So the table is the whole surface — a route that is not in
it cannot be reached through the bridge, however the request is phrased.

Two tiers, because the owner asked for two switches:

* ``operate`` — what the screens do: conversations, runs, boards, memory, files, git. Runs started
  this way carry the owner's configured posture, and a body that tries to widen it is refused.
* ``full`` — what the screens reserve for the person: answering approvals, editing settings,
  running a command outside the agent's governance. Listed and served only when the second switch
  is on.

And one exclusion that no switch lifts: credentials. The routes that take or return a key, a token
or a share link are simply absent (``/api/config/pool``, ``/api/config/test``, ``POST /api/mcp``,
sharing), settings edits refuse credential names (:func:`is_secret_setting`), and every response
passes through :func:`scrub` before it leaves.

Pure data and pure functions — no FastAPI, no network — so both ends import it and a test can read
the whole contract without starting anything.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

Tier = Literal["operate", "full"]


@dataclass(frozen=True)
class BridgeRoute:
    """One reachable app route."""

    method: str
    path: str
    """The app's own route template, e.g. ``/api/code/sessions/{session_id}``."""
    tier: Tier = "operate"
    stream: bool = False
    """The route answers with server-sent events: the bridge runs it as a job (see ``BridgeJobs``)."""
    seams: bool = False
    """The body is a ``CodeSeams``: in the operate tier the bridge sets the posture itself."""
    doc: str = ""


def _r(method: str, path: str, doc: str, **kw: Any) -> BridgeRoute:
    return BridgeRoute(method=method, path=path, doc=doc, **kw)


#: Route id -> route. The id is ``<area>.<action>``; the MCP server turns each area into one tool
#: whose ``action`` is an enum of that area's actions, so the tool list is derived, never kept twice.
ROUTES: dict[str, BridgeRoute] = {
    # --- projects (the Code screen's sidebar) ---
    "projects.list": _r("GET", "/api/code/workspaces", "The projects the owner added."),
    "projects.add": _r("POST", "/api/code/workspaces", "Register a project. body: {path, alias?}"),
    "projects.remove": _r("DELETE", "/api/code/workspaces", "Forget a project. params: {path}"),
    "projects.delete_conversations": _r(
        "DELETE",
        "/api/code/projects",
        "Delete every conversation of a project. params: {workspace}",
    ),
    # --- conversations (Code screen) ---
    "conversations.list": _r(
        "GET", "/api/code/sessions", "Past coding conversations, newest first."
    ),
    "conversations.read": _r(
        "GET",
        "/api/code/sessions/{session_id}",
        "One conversation as exchanges. params: {session_id}",
    ),
    "conversations.raw": _r(
        "GET", "/api/code/sessions/{session_id}/raw", "The stored file as-is. params: {session_id}"
    ),
    "conversations.fork": _r(
        "POST", "/api/code/sessions/{session_id}/fork", "Copy a conversation. params: {session_id}"
    ),
    "conversations.delete": _r(
        "DELETE", "/api/code/sessions/{session_id}", "Delete a conversation. params: {session_id}"
    ),
    "conversations.turn": _r(
        "GET",
        "/api/code/turns/{turn_id}",
        "What a turn emitted, for a lost stream. params: {turn_id}",
    ),
    "conversations.send": _r("POST", "/api/code/turn", "One coding turn.", stream=True, seams=True),
    # --- background works started from a conversation ---
    "works.list": _r(
        "GET",
        "/api/code/sessions/{session_id}/works",
        "A conversation's works. params: {session_id}",
    ),
    "works.session": _r(
        "GET", "/api/code/works/{work_id}/session", "A work's own transcript. params: {work_id}"
    ),
    "works.stop": _r("POST", "/api/code/works/{work_id}/stop", "Stop a work. params: {work_id}"),
    "works.undo": _r("POST", "/api/code/works/{work_id}/undo", "Undo a work. params: {work_id}"),
    # --- autonomous runs (Work screen) ---
    "runs.list": _r("GET", "/api/runs", "Run receipts, newest first. params: {workspace?}"),
    "runs.start": _r(
        "POST",
        "/api/runs",
        "Start a run. body: {task, workspace?, model?, max_attempts?}",
        stream=True,
        seams=True,
    ),
    "runs.cancel": _r("POST", "/api/runs/{run_id}/cancel", "Stop a run. params: {run_id}"),
    "runs.paused": _r("GET", "/api/runs/paused", "Runs paused for a person's answer."),
    # --- approvals: reading is operate, answering is full ---
    "approvals.list": _r("GET", "/api/approvals", "Questions waiting for the owner, oldest first."),
    "approvals.decisions": _r(
        "GET", "/api/decisions", "Recent governance decisions. params: {limit?}"
    ),
    # --- memory ---
    "memory.search": _r("GET", "/api/memory", "Search memory. params: {q, k?}"),
    "memory.layers": _r("GET", "/api/memory/layers", "Memory by layer."),
    "memory.profile": _r("GET", "/api/memory/profile", "The owner profile and persona facts."),
    "memory.add": _r("POST", "/api/memory", "Remember a fact. body: {content, kind?, project?}"),
    "memory.delete": _r("DELETE", "/api/memory/{item_id}", "Forget a fact. params: {item_id}"),
    # --- the Chat screen ---
    "chat.list": _r("GET", "/api/sessions", "Chat conversations."),
    "chat.read": _r("GET", "/api/sessions/{session_id}", "One chat. params: {session_id}"),
    "chat.new": _r("POST", "/api/sessions", "Start an empty chat."),
    "chat.delete": _r(
        "DELETE", "/api/sessions/{session_id}", "Delete a chat. params: {session_id}"
    ),
    "chat.send": _r(
        "POST", "/api/chat/stream", "One chat turn. body: {message, session_id?}", stream=True
    ),
    # --- agents (registry and batches) ---
    "agents.registry": _r("GET", "/api/agents/registry", "Saved agent definitions."),
    "agents.schema": _r("GET", "/api/agents/schema", "The batch request's limits."),
    "agents.design": _r("POST", "/api/agents/design", "Propose an agent. body: {description}"),
    "agents.delete": _r(
        "DELETE", "/api/agents/registry/{agent_id}", "Delete a saved agent. params: {agent_id}"
    ),
    "agents.batch": _r(
        "POST",
        "/api/agents",
        "Run tasks in parallel worktrees. body: {tasks:[{task}], workspace?}",
        stream=True,
        seams=True,
    ),
    "agents.cancel": _r(
        "POST", "/api/agents/{batch_id}/cancel", "Stop a batch. params: {batch_id}; body: {index?}"
    ),
    # --- orchestration ---
    "orchestration.approaches": _r(
        "GET", "/api/orchestration/approaches", "The approaches offered."
    ),
    "orchestration.schema": _r("GET", "/api/orchestration/schema", "Request limits."),
    "orchestration.delegations": _r(
        "GET", "/api/orchestration/delegations", "The delegation ledger."
    ),
    "orchestration.preview": _r(
        "POST", "/api/orchestration/preview", "Plan without running. body: {task, workspace?}"
    ),
    "orchestration.runs": _r("GET", "/api/orchestration/runs", "Past orchestrated runs."),
    "orchestration.run": _r(
        "GET", "/api/orchestration/runs/{run_id}", "One orchestrated run. params: {run_id}"
    ),
    "orchestration.cancel": _r(
        "POST", "/api/orchestration/runs/{run_id}/cancel", "Stop one. params: {run_id}"
    ),
    "orchestration.hierarchy": _r(
        "POST",
        "/api/orchestration/hierarchy",
        "Run a hierarchy. body: {task, workspace?}",
        stream=True,
    ),
    "orchestration.crew": _r(
        "POST",
        "/api/orchestration/crew",
        "Run a crew. body: {task, workers:[{name, instruction}], workspace?}",
        stream=True,
        seams=True,
    ),
    "lifecycle.start": _r(
        "POST",
        "/api/lifecycle",
        "Plan, build, test, review. body: {task, workspace?}",
        stream=True,
        seams=True,
    ),
    "lifecycle.cancel": _r("POST", "/api/lifecycle/{run_id}/cancel", "Stop it. params: {run_id}"),
    # --- kanban ---
    "kanban.board": _r("GET", "/api/kanban", "The board, by column."),
    "kanban.add_card": _r("POST", "/api/kanban/cards", "Add a card. body: {title, action?, lane?}"),
    "kanban.move_card": _r(
        "PATCH", "/api/kanban/cards/{card_id}", "Move a card. params: {card_id}; body: {column}"
    ),
    "kanban.delete_card": _r("DELETE", "/api/kanban/cards/{card_id}", "Delete. params: {card_id}"),
    "kanban.run": _r(
        "POST",
        "/api/kanban/run",
        "Work the board. body: {limit?, workspace?, workers?}",
        stream=True,
    ),
    # --- spec projects (spec -> cards) ---
    "spec_projects.list": _r("GET", "/api/projects", "Spec-driven projects."),
    "spec_projects.read": _r("GET", "/api/projects/{project_id}", "One. params: {project_id}"),
    "spec_projects.draft": _r("POST", "/api/projects/draft", "Draft a spec. body: {idea}"),
    "spec_projects.spec": _r("POST", "/api/projects/spec", "Write a spec file. body: {...}"),
    "spec_projects.create": _r(
        "POST", "/api/projects", "Start a project. body: {spec, workspace?, max_iterations?}"
    ),
    "spec_projects.step": _r(
        "POST", "/api/projects/{project_id}/step", "Advance one step. params: {project_id}"
    ),
    # --- cron ---
    "cron.list": _r("GET", "/api/cron", "Scheduled jobs."),
    "cron.results": _r("GET", "/api/cron/results", "Recent results."),
    "cron.silence": _r("GET", "/api/cron/silence", "Jobs that went quiet."),
    "cron.create": _r(
        "POST", "/api/cron", "Schedule a job. body: {name, schedule, action, workspace?}"
    ),
    "cron.enable": _r("POST", "/api/cron/{job_id}/enable", "Enable. params: {job_id}"),
    "cron.disable": _r("POST", "/api/cron/{job_id}/disable", "Disable. params: {job_id}"),
    "cron.delete": _r("DELETE", "/api/cron/{job_id}", "Delete. params: {job_id}"),
    # --- skills ---
    "skills.list": _r("GET", "/api/skills", "Learned skills."),
    "skills.retire": _r("POST", "/api/skills/{name}/retire", "Retire a skill. params: {name}"),
    "skills.catalog": _r("GET", "/api/skills/catalog", "Installable skill bundles."),
    "skills.install": _r(
        "POST", "/api/skills/catalog/{name}/install", "Install a bundle. params: {name}"
    ),
    "skills.bundles": _r("GET", "/api/skills/bundles", "Installed bundles."),
    "skills.bundle_status": _r(
        "POST",
        "/api/skills/bundles/{name}/status",
        "Enable/disable. params: {name}; body: {enabled}",
    ),
    "skills.bundle_delete": _r("DELETE", "/api/skills/bundles/{name}", "Remove. params: {name}"),
    "skills.library": _r("GET", "/api/skills/library", "The skill library."),
    "skills.library_card": _r("GET", "/api/skills/library/{name}", "One card. params: {name}"),
    "skills.library_import": _r(
        "POST", "/api/skills/library/{name}/import", "Import a card. params: {name}"
    ),
    # --- files and git (the workspace views) ---
    "files.browse": _r("GET", "/api/fs/browse", "List directories. params: {path?}"),
    "files.tree": _r("GET", "/api/fs/tree", "A workspace tree. params: {workspace?}"),
    "files.read": _r("GET", "/api/fs/file", "Read a file. params: {path, workspace?}"),
    "files.write": _r("PUT", "/api/fs/file", "Write a file. body: {path, content, workspace?}"),
    "files.search": _r("POST", "/api/fs/search", "Search text. body: {query, workspace?, glob?}"),
    "files.mkdir": _r("POST", "/api/fs/dir", "Make a directory. body: {path}"),
    "files.diagnostics": _r(
        "POST", "/api/lsp/diagnostics", "Language diagnostics. body: {path, text, workspace?}"
    ),
    "git.status": _r("GET", "/api/git/status", "Git status. params: {workspace?}"),
    "git.diff": _r("GET", "/api/git/diff", "Git diff. params: {workspace?, path?}"),
    "git.init": _r("POST", "/api/git/init", "git init. body: {workspace?}"),
    "git.commit": _r("POST", "/api/git/commit", "Commit. body: {message, paths, workspace?}"),
    "git.revert": _r("POST", "/api/git/revert", "Revert paths. body: {paths, workspace?}"),
    # --- planning helpers ---
    "planning.plan": _r("POST", "/api/plan", "Planner preview. body: {task}"),
    "planning.requirements": _r("POST", "/api/requirements", "Extract requirements. body: {task}"),
    "planning.decide": _r("POST", "/api/decide", "Typed questions over a state. body: {...}"),
    # --- background shell jobs: run_shell(background=true), outliving the turn that started them ---
    "shell_jobs.list": _r("GET", "/api/jobs", "Every job, newest first, with the end of its log."),
    "shell_jobs.read": _r(
        "GET",
        "/api/jobs/{job_id}",
        "One job and a bounded slice of its log. params: {job_id, tail_lines?, head_lines?}",
    ),
    "shell_jobs.stop": _r(
        "POST", "/api/jobs/{job_id}/cancel", "Kill a job and everything it started. params: {job_id}"
    ),
    # --- cost, quality, health ---
    "insights.usage": _r("GET", "/api/usage", "Spend and tokens, by day, model and session."),
    "insights.worth": _r(
        "GET", "/api/code/worth", "What configurations were worth. params: {workspace?}"
    ),
    "insights.benchmarks": _r("GET", "/api/benchmarks", "Benchmark results."),
    "insights.maturity": _r("GET", "/api/maturity", "Maturity report."),
    "insights.resources": _r("GET", "/api/resources", "Machine resources."),
    "insights.doctor": _r("GET", "/api/doctor", "Health checks."),
    # --- how the app is set up (credentials masked out by `scrub`) ---
    "app.config": _r("GET", "/api/config", "Settings, with every credential reduced to set/unset."),
    "app.models": _r("GET", "/api/models", "Models on offer."),
    "app.models_local": _r("GET", "/api/models/local", "Local runtimes."),
    "app.models_ollama": _r("GET", "/api/models/ollama", "Ollama models."),
    "app.tools": _r("GET", "/api/tools", "The agent's tools."),
    "app.mcp_servers": _r("GET", "/api/mcp", "Configured MCP servers."),
    "app.mcp_catalog": _r("GET", "/api/mcp/catalog", "The MCP catalog."),
    "app.mcp_test": _r("POST", "/api/mcp/{name}/test", "Probe a configured server. params: {name}"),
    "app.messaging": _r("GET", "/api/messaging", "Messaging adapters."),
    "app.messaging_stop": _r(
        "POST", "/api/messaging/{platform}/stop", "Stop an adapter. params: {platform}"
    ),
    "app.governance_audit": _r("GET", "/api/governance/audit", "The governance audit log."),
    "app.governance_injection": _r("GET", "/api/governance/injection", "Injection report."),
    "app.governance_sandbox": _r("GET", "/api/governance/sandbox", "Sandbox state."),
    "app.posture": _r(
        "POST", "/api/code/posture", "Describe a posture. body: {reach, approval, workspace?}"
    ),
    "app.roles": _r("POST", "/api/code/roles", "Resolve role models. body: {profile?}"),
    "app.instructions": _r("GET", "/api/instructions", "The agent's identity and instructions."),
    "app.version": _r("GET", "/api/version", "Running version and whether an update exists."),
    "app.dictation": _r("GET", "/api/dictation", "Dictation capability."),
    "app.vision": _r("GET", "/api/vision", "Vision capability."),
    # ================= full control =================
    "approve.approval": _r(
        "POST",
        "/api/approvals/{request_id}",
        "Answer a pending question. params: {request_id}; body: {approved: true|false}",
        tier="full",
    ),
    "approve.paused_run": _r(
        "POST",
        "/api/runs/{thread_id}/respond",
        "Answer a paused run. params: {thread_id}; "
        "body: {action: accept|edit|respond|ignore, answer?, feedback?}",
        tier="full",
    ),
    "approve.spec_project": _r(
        "POST",
        "/api/projects/{project_id}/approve",
        "Approve a project's gated step. params: {project_id}",
        tier="full",
    ),
    "approve.spec_project_deny": _r(
        "POST", "/api/projects/{project_id}/deny", "Deny it. params: {project_id}", tier="full"
    ),
    "approve.skill": _r(
        "POST", "/api/skills/{name}/approve", "Approve a learned skill. params: {name}", tier="full"
    ),
    "approve.label_decision": _r(
        "POST",
        "/api/decisions/{decision_id}/label",
        "Label a decision. params: {decision_id}; body: {...}",
        tier="full",
    ),
    "settings.edit": _r(
        "PATCH",
        "/api/config",
        "Edit settings: body {ENV_NAME: value}. Credentials are refused.",
        tier="full",
    ),
    "settings.instructions": _r(
        "PUT",
        "/api/instructions",
        "Replace the agent's identity. body: {name, language, instructions}",
        tier="full",
    ),
    "settings.agent_upsert": _r(
        "PUT",
        "/api/agents/registry",
        "Save an agent definition (with its tool grants).",
        tier="full",
    ),
    "settings.mcp_remove": _r(
        "DELETE", "/api/mcp/{name}", "Remove an MCP server. params: {name}", tier="full"
    ),
    "settings.messaging_start": _r(
        "POST",
        "/api/messaging/{platform}/start",
        "Start an adapter. params: {platform}",
        tier="full",
    ),
    "settings.exec": _r(
        "POST",
        "/api/fs/exec",
        "Run a shell command in the Runner, OUTSIDE the agent's governance. "
        "body: {command, workspace?, cwd?, timeout?}",
        tier="full",
        stream=True,
    ),
    "settings.exec_cancel": _r("POST", "/api/fs/exec/cancel", "Stop it. body: {id}", tier="full"),
}

#: Body fields that widen what a run may do. In the operate tier a body carrying any of them with a
#: truthy value is refused: ``verify`` and ``provider_command`` are shell commands, ``provider`` hands
#: the workspace to another agent with its own tools, ``auto_approve`` answers a project's gates
#: without the person, and ``posture``/``allow_host_exec`` are the posture itself.
FULL_ONLY_BODY_KEYS = frozenset(
    {"posture", "allow_host_exec", "provider", "provider_command", "verify", "auto_approve"}
)

#: The two switches themselves. Never editable through the bridge, full control or not: a client
#: that could write them could widen its own access.
BRIDGE_SETTINGS = frozenset({"CHIMERA_DESKTOP_BRIDGE", "CHIMERA_DESKTOP_BRIDGE_FULL"})

_SECRET_NAME = re.compile(
    r"(API_?KEY|_KEYS$|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE|WEBHOOK)", re.IGNORECASE
)
#: Response keys whose string value is a credential, or part of one.
_SECRET_FIELD = re.compile(
    r"^(token|.*_token|secret|.*_secret|password|api_?key|.*_api_key|webhook|.*_webhook(_url)?)$",
    re.IGNORECASE,
)
MASK = "[redacted]"


#: File names that hold credentials. A bridge call that names one — to read, write, diff, commit or
#: search — is refused at every tier: the settings screen masks keys, and a file view that returned
#: the `.env` beside it would be the same key by a side door.
_SECRET_FILE = re.compile(
    r"^(\.env(\..*)?|.*\.(pem|key|p12|pfx|keystore|jks)|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|"
    r"credentials(\..*)?|\.netrc|\.pypirc|\.npmrc|\.git-credentials|secrets?(\..*)?|"
    r"desktop-bridge\.json)$",
    re.IGNORECASE,
)


def is_secret_file(path: str) -> bool:
    """Whether any component of ``path`` names a credential file (``.env``, a private key, ...)."""
    parts = re.split(r"[\\/]+", str(path))
    return any(_SECRET_FILE.match(part) for part in parts if part)


def is_secret_setting(name: str) -> bool:
    """Whether a settings name holds a credential — refused by ``settings.edit`` with any switch on.

    The config API's own list first (every provider key, the server token, the messaging tokens),
    then the name shape, so a credential added later is caught even before anyone lists it here.
    """
    from chimera.api.config_api import _SECRET_KEYS
    from chimera.providers.discovery import provider_from_env_var

    return (
        name in _SECRET_KEYS
        or provider_from_env_var(name) is not None
        or bool(_SECRET_NAME.search(name))
    )


def full_only_keys_in(body: Any) -> list[str]:
    """Every widening field carried with a truthy value, anywhere in ``body``, sorted."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in FULL_ONLY_BODY_KEYS and value not in (None, False, "", [], {}):
                    found.add(str(key))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(body)
    return sorted(found)


def scrub(value: Any, secrets: Iterable[str] = ()) -> Any:
    """``value`` with credential-named fields removed and credential-shaped strings masked.

    Three nets: a field whose NAME marks a credential is dropped whole (a token, the ``hint`` of a
    masked key); every string goes through the log redactor, which knows the process's own secret
    values and the common key shapes; and any exact ``secrets`` passed in — the bridge token — are
    masked wherever they appear.
    """
    from chimera.core.redact import redact

    extra = sorted({s for s in secrets if s}, key=len, reverse=True)

    def text(s: str) -> str:
        for secret in extra:
            s = s.replace(secret, MASK)
        return redact(s)

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            # `{set, hint}` is how the settings API reports a key: the hint is its last four
            # characters — harmless on the owner's screen, and still a piece of a key. A `hint`
            # anywhere else (the doctor's "run this") is advice and stays.
            masked_key = "set" in node and "hint" in node
            return {
                k: walk(v)
                for k, v in node.items()
                if not (
                    isinstance(v, str)
                    and (_SECRET_FIELD.match(str(k)) or (masked_key and k == "hint"))
                )
            }
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, str):
            return text(node)
        return node

    return walk(value)


def areas(tier: Tier | None = None) -> dict[str, list[str]]:
    """Area -> its action names, in table order; ``tier`` narrows to one tier's routes."""
    out: dict[str, list[str]] = {}
    for route_id, route in ROUTES.items():
        if tier is not None and route.tier != tier:
            continue
        area, _, action = route_id.partition(".")
        out.setdefault(area, []).append(action)
    return out
