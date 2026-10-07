"""What the desktop bridge may reach, as data: one table the server enforces and the MCP tools list.

The bridge (`chimera/api/desktop_bridge.py`) forwards a call to the app's own routes in-process. It
never forwards a URL the caller wrote: the caller names a ROUTE ID from this table, and the server
builds the path from the template here. So the table is the whole surface — a route that is not in
it cannot be reached through the bridge, however the request is phrased.

Two tiers, because the owner asked for two switches:

* ``operate`` — what the screens do: conversations, runs, boards, memory, files, git. Runs started
  this way carry the owner's configured posture and models, and a body that tries to widen the one
  or choose the other is refused — at this tier and at the next (:func:`wider_than`,
  :func:`model_choices_in`).
* ``full`` — what the screens reserve for the person: answering approvals, editing the settings
  that are not the owner's, replacing the agent's identity. Listed and served only when the second
  switch is on.

Two exclusions that no switch lifts. The routes that WIDEN what the agent may reach — granting a
folder's commands, running a command outside governance, starting a messaging bot, saving an agent
with its tool grants — are the owner's decision in the app (:data:`OWNER_DECISION_ROUTES`); the
bridge answers them with that sentence at every tier. And credentials. The routes that take or return a key, a token
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
    "projects.list": _r(
        "GET",
        "/api/code/workspaces",
        "The projects the owner added, with where commands are granted (shell_granted).",
    ),
    "projects.add": _r("POST", "/api/code/workspaces", "Register a project. body: {path, alias?}"),
    "projects.remove": _r("DELETE", "/api/code/workspaces", "Forget a project. params: {path}"),
    # Pinning and hiding only ever narrow what the agent may do (hiding revokes a grant), so they
    # are operate. GRANTING commands in a folder is the posture itself: it is the owner's, in the
    # app, and not in this table at all (`OWNER_DECISION_ROUTES`).
    "projects.flag": _r(
        "PATCH",
        "/api/code/workspaces",
        "Pin or hide a project (hiding revokes its grant). body: {path, pinned?, hidden?}",
    ),
    "projects.delete_conversations": _r(
        "DELETE",
        "/api/code/projects",
        "Delete every conversation of a project. params: {workspace}",
    ),
    # --- conversations (Code screen) ---
    "conversations.list": _r(
        "GET",
        "/api/code/sessions",
        "Past coding conversations, newest first, each with its state. params: {archived?}",
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
    # Moves a conversation out of the list and back; touches no file. Not `seen`: an agent reading
    # a conversation is not the owner looking at its diff.
    "conversations.archive": _r(
        "POST",
        "/api/code/sessions/{session_id}/archive",
        "Archive a conversation. params: {session_id}",
    ),
    "conversations.unarchive": _r(
        "POST",
        "/api/code/sessions/{session_id}/unarchive",
        "Bring an archived conversation back. params: {session_id}",
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
    "conversations.running": _r(
        "GET", "/api/code/turns/running", "The coding turns running now, oldest first."
    ),
    # Steering a running turn is sending it a message, so it is held where `send` is: the operate
    # tier, and `guard_places` holds the turn's folder to the rule a continued conversation's is.
    "conversations.guidance": _r(
        "POST",
        "/api/code/turns/{turn_id}/guidance",
        "Steer a running turn: the agent reads the text between two steps, never during a tool "
        "call. params: {turn_id}; body: {text}",
    ),
    # Stopping only narrows: the step in progress ends and nothing further runs.
    "conversations.stop": _r(
        "POST", "/api/code/turns/{turn_id}/stop", "Stop a running turn. params: {turn_id}"
    ),
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
        "Start a run on the owner's models and posture. body: {task, workspace?, max_attempts?}",
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
        "Switch a bundle off. params: {name}; body: {status: inactive}. Switching one on puts "
        "its text in every prompt: that is approve.skill_bundle, with Full control.",
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
        "POST",
        "/api/jobs/{job_id}/cancel",
        "Kill a job and everything it started. params: {job_id}",
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
    # Moved here from the `skills` area, and to Full: switching a bundle ON is approving a
    # stranger's instructions into the prompt — the same decision `approve.skill` is for a learned
    # card — and with skills now uploadable, a stranger nobody has vetted. In the operate tier a
    # client could install a pending skill and switch it on in the next call, and "lands pending
    # until the owner turns it on" would have held only on the owner's own screen. In this area
    # because Full routes live in `approve` and `settings` only: one tool per area, one tier per tool.
    "approve.skill_bundle": _r(
        "POST",
        "/api/skills/bundles/{name}/status",
        "Switch an installed skill bundle on or off. params: {name}; "
        "body: {status: active|inactive}",
        tier="full",
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
        "Edit settings: body {ENV_NAME: value}. Which model answers (every *_MODEL, the fallback "
        "chain, the fusion panel, judge and synthesizer, the cost mode, the cascade, verified "
        "answers) and whether the app runs scheduled jobs are only SUGGESTED: nothing is written, "
        "the owner gets a card in the app with the current and the proposed value and decides "
        "there (send them alone, not mixed with other settings). Credentials are refused, and so "
        "are the owner's settings: posture, guards, approvals, where the agent may reach, who may "
        "reach it, and where prompts may go.",
        tier="full",
    ),
    "settings.instructions": _r(
        "PUT",
        "/api/instructions",
        "Replace the agent's identity. body: {name, language, instructions}",
        tier="full",
    ),
    "settings.mcp_remove": _r(
        "DELETE", "/api/mcp/{name}", "Remove an MCP server. params: {name}", tier="full"
    ),
    "settings.exec_cancel": _r(
        "POST",
        "/api/fs/exec/cancel",
        "Stop a command the owner started in the Runner. body: {id}",
        tier="full",
    ),
}

#: The app routes that WIDEN what the agent may reach, and the sentence the bridge answers with —
#: at every tier, full control included. They were Full routes until 2026-10-04, when the owner
#: decided otherwise: a folder's command grant and a command run outside governance are the posture
#: itself; a messaging bot started is a new way INTO the agent, for whoever its allowlist admits;
#: and a saved agent carries its own tool grants, so saving one is granting tools. Each is one call
#: for a client that read one poisoned page, and "the owner can turn full control off afterwards"
#: comes after the reach was already handed out.
#:
#: Out of :data:`ROUTES`, so the MCP server never lists them and nothing can forward to their path;
#: named here, so a client that asks gets the reason instead of "no such route". The owner's own
#: screens call the same app routes as before — only the bridge's door is shut.
OWNER_DECISION_ROUTES: dict[str, str] = {
    "settings.folder_grant": (
        "settings.folder_grant is the owner's decision: whether the agent may run commands in a "
        "folder is granted or revoked by the owner in the app (Settings > Folders, or the Code "
        "screen's switch), never through the bridge."
    ),
    "settings.exec": (
        "settings.exec is the owner's decision: a command outside the agent's governance is run by "
        "the owner in the app's Runner, never through the bridge."
    ),
    "settings.messaging_start": (
        "settings.messaging_start is the owner's decision: a messaging bot is a new way to reach "
        "the agent, and the owner starts it in the app (Settings > Messaging), never through the "
        "bridge."
    ),
    "settings.agent_upsert": (
        "settings.agent_upsert is the owner's decision: a saved agent carries its own tool grants, "
        "so the owner saves it in the app (the Agents screen), never through the bridge."
    ),
}

#: Body fields that widen what a run may do: ``verify`` and ``provider_command`` are shell commands,
#: ``provider`` hands the workspace to another agent with its own tools, ``auto_approve`` answers a
#: project's gates without the person, and ``posture``/``allow_host_exec`` are the posture itself.
#: Until 2026-10-04 they were refused below Full control and accepted at it. Since then no tier
#: widens: :func:`model_choices_in` refuses ``provider``/``provider_command`` everywhere, and
#: :func:`wider_than` accepts the rest only when they are no wider than the owner's posture.
FULL_ONLY_BODY_KEYS = frozenset(
    {"posture", "allow_host_exec", "provider", "provider_command", "verify", "auto_approve"}
)

#: Body fields that choose which model (or which outside agent) does the work. The owner's decision
#: of 2026-10-04: a run, turn, chat or batch the bridge starts uses the CONFIGURED models, at every
#: tier — the model choices are the owner's to write (`SUGGESTABLE_SETTINGS`), and a per-run field
#: that picked one would be the same choice made one run at a time. The audit of every body the
#: bridge forwards (CodeTurnRequest, RunRequest, AgentsRequest, CrewRunIn, LifecycleRunIn,
#: HierarchyRunIn, KanbanRunIn, ChatRequest, and the CodeSeams they share) found these:
MODEL_CHOICE_KEYS = frozenset(
    {
        "model",  # the turn's, run's, batch's or board's model
        "roles",  # the role plan: explore/plan/edit/review models, fused plan and review
        "profile",  # the economy/balanced/max preset, which picks the role models
        "fuse",  # whether a panel of models answers
        "fusion_panel",
        "fusion_judge",
        "fusion_synthesizer",
        "cascade",  # weak -> mid -> fusion routing for the run
        "verifier_model",  # the hierarchy's verifier
        "provider",  # another agent (an outside CLI) instead of Chimera's models
        "provider_command",
        "retry_of",  # "redone on another model by the owner's choice" - the owner's sentence
    }
)

#: Routes whose body only DESCRIBES (what posture would mean, which models a profile would give):
#: they start nothing, so naming a model or a posture there chooses nothing. And the settings
#: edit, whose keys are setting names and is policed on its own.
DESCRIBE_ONLY_ROUTES = frozenset({"app.posture", "app.roles", "settings.edit"})

_REACH_ORDER = {"read_only": 0, "workspace": 1, "workspace_shell": 2}
_APPROVAL_STRICTNESS = {"never": 0, "suspicious": 1, "always": 2}
#: What a posture object means for a field it leaves out (`chimera/api/posture.py`).
_POSTURE_DEFAULTS = {"reach": "workspace", "approval": "suspicious"}


def _truthy(value: Any) -> bool:
    return value not in (None, False, "", [], {})


def model_choices_in(body: Any) -> list[str]:
    """Every model-choosing field carried with a value, anywhere in ``body``, sorted."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in MODEL_CHOICE_KEYS and _truthy(value):
                    found.add(str(key))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(body)
    return sorted(found)


def wider_than(body: Any, *, reach: str, approval: str) -> list[str]:
    """Every field of ``body`` that would make a run reach further than the owner's posture.

    Equal or narrower passes: a client may ask for ``read_only``, or for approval ``always``, where
    the owner allows more. Wider is refused — a reach past the owner's, an approval looser than the
    owner's, host execution or a ``verify`` shell command where the owner's reach has no shell, and
    ``auto_approve`` always (it answers gates without the person), and ``deliver_to`` always (the
    webhook a scheduled job posts its answers to). A posture value this module does not know is
    wider by definition.
    """
    found: set[str] = set()
    shell = reach == "workspace_shell"

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "posture" and _truthy(value):
                    if not isinstance(value, dict):
                        found.add("posture")
                    else:
                        r = str(value.get("reach") or _POSTURE_DEFAULTS["reach"])
                        a = str(value.get("approval") or _POSTURE_DEFAULTS["approval"])
                        if _REACH_ORDER.get(r, 99) > _REACH_ORDER.get(reach, -1):
                            found.add("posture.reach")
                        if _APPROVAL_STRICTNESS.get(a, -1) < _APPROVAL_STRICTNESS.get(approval, 99):
                            found.add("posture.approval")
                elif key in {"allow_host_exec", "verify"} and _truthy(value) and not shell:
                    found.add(str(key))
                elif key == "auto_approve" and _truthy(value):
                    found.add("auto_approve")
                elif key == "deliver_to" and _truthy(value):
                    # A scheduled job's answer posted to a webhook the CLIENT names: an outbound
                    # channel for whatever the job reads, firing unattended. Where the owner's data
                    # goes is the owner's (the approval webhook is owner-only for the same reason);
                    # a job created through the bridge reports in the app (audit of 2026-10-04).
                    found.add("deliver_to")
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(body)
    return sorted(found)

#: The two switches themselves. Never editable through the bridge, full control or not: a client
#: that could write them could widen its own access.
BRIDGE_SETTINGS = frozenset({"CHIMERA_DESKTOP_BRIDGE", "CHIMERA_DESKTOP_BRIDGE_FULL"})

#: The two that narrow sharing. Each only narrows from the screen, but a client that could write it
#: could undo the owner's narrowing — turn sharing back on, or make new links never expire — and so
#: open a door the owner had shut.
SHARING_SETTINGS = frozenset({"CHIMERA_SHARING", "CHIMERA_SHARE_EXPIRY_HOURS"})

#: What a run may do, and what stands between it and doing it: the posture, the trust kernel and its
#: denylist, the chat's guard, and everything that answers or routes an approval. The decision pair
#: is here because it IS the governance band's instrument (`chimera/governance/band.py`): a model
#: the band has no calibration map for is read as a prior and thresholds nothing, so swapping it
#: switches the band off without touching `CHIMERA_GOVERNANCE`. The webhook is also refused by its
#: name's shape (:func:`is_secret_setting`); it is listed for what it does — whoever receives the
#: question answers it — so the refusal does not depend on a regular expression.
GUARD_SETTINGS = frozenset(
    {
        "CHIMERA_REACH",
        "CHIMERA_APPROVAL",
        "CHIMERA_HOST_EXEC",
        "CHIMERA_GOVERNANCE",
        "CHIMERA_TAINT_ROPE_LITE",
        "CHIMERA_TOOL_DENYLIST",
        "CHIMERA_GUARD_CHAT",
        "CHIMERA_APPROVAL_WEBHOOK",
        "CHIMERA_DECISION_BACKEND",
        "CHIMERA_DECISION_MODEL",
        # The only brake on what unattended jobs spend (`chimera/scheduler/job_runner.py`).
        "CHIMERA_DAILY_USD_CAP",
        # Whether a typed dollar ceiling is strict (owner's decision, 2026-10-05). On only tightens,
        # but a client that could switch it off would loosen a limit the owner chose to hold hard.
        "CHIMERA_STRICT_SPEND_CAP",
        # The project-pack switch narrows: on, an accepted pack takes skills, servers and tools
        # away; switching it off hands them back (study 29, P7.6).
        "CHIMERA_PROJECT_PACK",
        # Two rules that only add questions (study 30, S30-27 and S30-28): a client that could
        # switch one off would take a question away from the owner.
        "CHIMERA_EXFIL_HOST_PATH",
        "CHIMERA_SHELL_FETCH_GUARD",
        # The owner's lifecycle hooks (`docs/hooks-threat-model.md`). On, a hook only tightens; a
        # client that could switch hooks off would remove the guards the owner wrote, and one that
        # could switch the host companion on would let shell hooks run outside the sandbox.
        "CHIMERA_HOOKS",
        "CHIMERA_HOOKS_HOST_EXEC",
    }
)

#: Where the agent may reach: the sandbox and its network, the hosts a run holding untrusted content
#: may still send a query-string GET to, the browser's sites and local ports (emptying the site list WIDENS it to any public
#: site), and the MCP servers loaded into every registry at boot.
REACH_SETTINGS = frozenset(
    {
        "CHIMERA_SANDBOX",
        "CHIMERA_SANDBOX_NETWORK",
        "CHIMERA_EGRESS_ALLOW",
        "CHIMERA_BROWSER_SITES",
        "CHIMERA_BROWSER_LOCAL_PORTS",
        "CHIMERA_MCP_AUTOLOAD",
        # Gives the agent `open_pull_request` (study 29, P8.1), a tool that publishes the owner's
        # code to a remote: a client that could switch it on would widen where that code may go.
        "CHIMERA_PULL_REQUESTS",
        # The first segment of every branch a run makes (`<prefix>/attempt-…`, study 29, P8.2). It
        # is also what the worktree cleanup force-deletes (`git branch -D <prefix>/attempt-*`,
        # remembered across changes in `worktree-prefixes.txt`), and the name a pull request's
        # branch carries to the remote: a client that could set it would choose which of the
        # owner's branches the cleanup deletes, and what pattern the pushed branch matches there.
        "CHIMERA_BRANCH_PREFIX",
    }
)

#: Who may reach the agent: whether the bots start with the app, and who each one answers. An empty
#: list means ANYONE (`chimera/server/allowlist.py`), so clearing one is the widest edit there is.
#: And what the bots carry out: the Discord attachment switch (study 29, P6.3) sends the files a turn
#: wrote — the owner's — to a channel, so turning it on is the owner's alone.
MESSAGING_SETTINGS = frozenset(
    {
        "CHIMERA_APP_MESSAGING",
        "CHIMERA_DISCORD_ATTACH_FILES",
        "CHIMERA_DISCORD_ALLOWED_USERS",
        "CHIMERA_TELEGRAM_ALLOWED_USERS",
        "CHIMERA_SLACK_ALLOWED_USERS",
        "CHIMERA_SIGNAL_ALLOWED_USERS",
        "CHIMERA_WHATSAPP_ALLOWED_NUMBERS",
    }
)

#: Where prompts may go. OpenRouter's two privacy fences (`allow` / `false` loosen them), and the
#: three base URLs: `CHIMERA_API_BASE` is sent on every call WITH the provider's key, so pointing it
#: elsewhere hands over the key and every prompt; the two local runtimes' URLs turn a model the
#: owner chose for staying on this machine into one that does not.
PRIVACY_SETTINGS = frozenset(
    {
        "CHIMERA_OPENROUTER_DATA_COLLECTION",
        "CHIMERA_OPENROUTER_ZDR",
        "CHIMERA_API_BASE",
        "CHIMERA_OLLAMA_BASE_URL",
        "CHIMERA_LM_STUDIO_BASE_URL",
        # Where the owner's keys live (study 29, P7.7). On, it only narrows where a key may be read
        # from, but a client that could switch it off would send the next key the owner types into
        # a plain-text file. Its name matches no credential pattern, so it has to be listed.
        "CHIMERA_KEY_VAULT",
        # Whether the agent's read tools may read Chimera's own `.env` and so put the provider keys
        # in front of the model (owner's decision, 2026-10-04). Off narrows; a client that could
        # write it could turn it back on.
        "CHIMERA_AGENT_READS_OWN_ENV",
        # Which repositories may start a code-writing job from an issue: a client that could add
        # one would choose where the agent writes. The webhook secrets are not here: they are a
        # credential (`config_api._SECRET_KEYS`), which the bridge refuses with any switch on.
        "CHIMERA_GITHUB_ISSUE_REPOSITORIES",
    }
)

#: Settings only the owner writes, full control or not, refused flat (403). The rule is the
#: project's: whatever WIDENS what the agent can reach, loosens a guard or a privacy fence, or
#: changes who answers an approval is the owner's decision — a client that could write one could
#: widen its own access, or undo a narrowing the owner chose. Full control is for operating the
#: app, not for setting its limits. The settings the bridge may SUGGEST are a separate set
#: (:data:`SUGGESTABLE_SETTINGS`); every other editable setting (caches, memory, display, the tool
#: switches that stay inside the reach above) stays writable: `tests/
#: test_the_bridge_may_not_write_the_settings_that_set_its_limits.py` holds the whole allowlist
#: classified, so a new setting cannot arrive unclassified.
OWNER_ONLY_SETTINGS = (
    BRIDGE_SETTINGS
    | SHARING_SETTINGS
    | GUARD_SETTINGS
    | REACH_SETTINGS
    | MESSAGING_SETTINGS
    | PRIVACY_SETTINGS
)

#: Which model or route a prompt goes to, and whether the app runs scheduled jobs: the owner's to
#: WRITE (decided 2026-10-04), but the bridge may SUGGEST a change. A `settings.edit` naming only
#: these writes nothing; it leaves a card for the owner (`chimera/governance/setting_suggestions.py`)
#: with the key, the value now and the value proposed, and the owner approves or refuses it in the
#: app. Why not a flat refusal like the set above: none of these widens reach — the model choices
#: stay among the providers the owner holds keys for, behind the privacy fences — but each one
#: decides what is spent and which vendor reads the owner's prompts, and the unattended scheduler
#: decides what runs while nobody watches. A client is often right about which model suits a task;
#: it is the owner who pays for it and reads what it costs.
#:
#: The audit of the editable allowlist that produced this list, key by key: every ``*_MODEL`` the
#: allowlist holds (default, weak, mid, orchestrator, embed, complete, voice, voice work — the embed
#: model reads every fact remembered, the completion model reads the code being typed), the
#: fallback chain, the three fusion roles, ``CHIMERA_COST_MODE`` (fills every unpinned rung of the
#: model ladder), ``CHIMERA_CASCADE`` (routes a turn weak -> mid -> fusion) and
#: ``CHIMERA_VERIFIED_ANSWERS`` (sends a grounded answer to the decision backend and, when it is
#: not supported, has the stronger model answer instead). Left out on purpose:
#: ``CHIMERA_DECISION_MODEL`` is already refused flat (it is the governance band's instrument,
#: :data:`GUARD_SETTINGS`); ``CHIMERA_SEMANTIC_MEMORY`` and ``CHIMERA_DECIDE_TOOL`` switch a
#: feature on whose model is one of the keys here or one of the owner's, so the choice of model
#: stays guarded through that key; ``CHIMERA_RESEARCH_AGENT`` runs on the models already chosen.
SUGGESTABLE_SETTINGS = frozenset(
    {
        "CHIMERA_DEFAULT_MODEL",
        "CHIMERA_WEAK_MODEL",
        "CHIMERA_MID_MODEL",
        "CHIMERA_ORCHESTRATOR_MODEL",
        "CHIMERA_FALLBACK_MODELS",
        "CHIMERA_EMBED_MODEL",
        "CHIMERA_COMPLETE_MODEL",
        "CHIMERA_VOICE_MODEL",
        "CHIMERA_VOICE_WORK_MODEL",
        "CHIMERA_FUSION_PANEL",
        "CHIMERA_FUSION_JUDGE",
        "CHIMERA_FUSION_SYNTHESIZER",
        "CHIMERA_COST_MODE",
        "CHIMERA_CASCADE",
        "CHIMERA_VERIFIED_ANSWERS",
        # Whether the app runs the cron daemon at all — the jobs that run unattended.
        "CHIMERA_APP_CRON",
    }
)

#: The ASGI scope key the bridge sets on every request it forwards in-process (`asgi_call`). Set in
#: the SCOPE, not in a header: a header is whatever a client sends, and a scope key exists only when
#: this process built the request itself — no request from a socket can carry it. A handler that
#: must tell the owner's screen from the bridge (answering a settings suggestion) reads it there.
VIA_BRIDGE_SCOPE_KEY = "chimera.via_desktop_bridge"

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
    """Whether any component of ``path`` names a credential file (``.env``, a private key, ...).

    Each component as Windows OPENS it (:func:`chimera.core.own_files.normal_name`): ``.env ``,
    ``.env.`` and ``.env::$DATA`` are the file ``.env``, and matching the raw text let all three read
    and write it through the bridge at the operate tier (review of 2026-10-04). An 8.3 short name
    (``ENV~1``) has no text to match; the bridge also checks the name the path RESOLVES to.
    """
    from chimera.core.own_files import normal_name

    parts = re.split(r"[\\/]+", str(path))
    return any(
        _SECRET_FILE.match(part) or _SECRET_FILE.match(normal_name(part))
        for part in parts
        if part
    )


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


def switches_off(body: Any) -> bool:
    """Whether a ``skills.bundle_status`` body can only switch a bundle OFF.

    Exactly ``{"status": "inactive"}`` and nothing else: the route's own model defaults ``status``
    to ``"active"``, so an empty body, a misspelled field or the old ``{enabled}`` shape all switch
    a bundle ON, and only the one shape that cannot is let through that route. Switching ON goes
    through ``approve.skill_bundle``, at Full control.
    """
    return isinstance(body, dict) and body == {"status": "inactive"}


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


#: The fields of a held MCP change that carry the server's own text. The hold exists to keep that
#: text away from a model until the owner approves it, so a bridge caller — an agent driving the
#: app — gets the shape of the change (tool, kind, which parts changed, the cues) and not the text.
HELD_TEXT_FIELDS = frozenset({"old_description", "new_description", "old_schema", "new_schema"})


def without_held_text(data: Any) -> Any:
    """An ``/api/mcp`` listing with the server-written text of every ``manifest_held`` removed.

    The owner's screen reads the same route and keeps the full diff; only the bridge's copy is cut.
    ``scrub`` does not cover this: it masks credentials, and a poisoned description is not one.
    """
    if not isinstance(data, dict):
        return data
    servers = data.get("servers")
    if not isinstance(servers, list):
        return data
    out = []
    for server in servers:
        held = server.get("manifest_held") if isinstance(server, dict) else None
        if isinstance(held, dict) and isinstance(held.get("changes"), list):
            changes = [
                {k: v for k, v in c.items() if k not in HELD_TEXT_FIELDS}
                if isinstance(c, dict)
                else c
                for c in held["changes"]
            ]
            server = {**server, "manifest_held": {**held, "changes": changes}}
        out.append(server)
    return {**data, "servers": out}


def scrub(value: Any, secrets: Iterable[str] = ()) -> Any:
    """``value`` with credential-named fields removed and credential-shaped strings masked.

    Three nets: a field whose NAME marks a credential is dropped whole (a token, the ``hint`` of a
    masked key); every string goes through the log redactor, which knows the process's own secret
    values and the common key shapes; and any exact ``secrets`` passed in — the bridge token — are
    masked wherever they appear.
    """
    from chimera.core import redact as redaction

    extra = sorted({s for s in secrets if s}, key=len, reverse=True)

    def text(s: str) -> str:
        # The token is minted at runtime and is not in the environment, so `redact` does not know
        # it: it gets the same net as a known secret here, encoded copies included. Masking it only
        # verbatim let its base64 or hex through to the MCP client (study 30 review).
        if extra:
            s = redaction.mask_known(s, extra, encoded=redaction.MASK_ENCODED)
        return redaction.redact(s)

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
