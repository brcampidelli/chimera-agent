"""Read/write the settings surface for the desktop app's Settings screen.

Security is the whole point of this module:

- **Secrets are never returned in cleartext.** ``read_config`` reports each credential as ``{set,
  hint}`` where the hint is at most the last 4 characters — enough to recognize which key is present,
  never the key itself. The server token reports only ``set`` (no hint at all).
- **Writes go to ``.env`` only, through an allowlist.** ``patch_config`` refuses any key that isn't a
  known setting or credential slot, so a request can't inject arbitrary lines into ``.env``. The value
  is written atomically and never logged.

This maps directly to the competitor's Model / API-Keys / Gateway settings panes.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from chimera.api.key_vault import (
    SCREEN_STORABLE,
    move_to_file,
    move_to_vault,
    vault_snapshot,
    write_credentials,
    write_env_value,
)
from chimera.config import Settings, get_settings, pinned_by_environment
from chimera.memory.backend import resolve_memory_backend
from chimera.providers.catalog import PROVIDERS
from chimera.providers.privacy import privacy_snapshot

# Credential env-vars (secret) and the non-secret settings the UI may edit. Anything outside this set
# is rejected by patch_config, so the endpoint can't be used to write arbitrary .env lines.
#: Credentials that buy a capability rather than a model — search, speech, images. Listed on the
#: settings screen beside the providers, and deliberately NOT offered by the first-run wizard: none
#: of them makes ``has_any_key`` true, so choosing one there would be a dead end that confirms.
# Two of these register a tool the moment the key is set; three do not, and the labels used to read
# alike. "Brave (web search)" sitting beside "Tavily (web search)" says they are the same kind of
# thing — paste the key and it works — when only one of them is. `chimera/tools/web.py` implements
# Tavily and nothing else, and the `.env.example` was already more honest than this screen: it
# marks Stability "(reserved)" while the label here said "Stability (images)".
#
# The first correction of those labels said "import its OpenAPI spec", and that was a second promise
# of the same kind. `chimera/integrations/openapi.py` exists, but its only caller outside the tests
# is `chimera schema-bench`, which counts schema tokens and registers nothing; no screen, command or
# setting turns a spec into a tool the agent can call, and nothing would hand it one of these keys
# if one did. So the three keys are what `.env.example` already called Stability: reserved. Setting
# one stores it and changes nothing. Kept rather than removed so a key already saved stays visible
# (and masked) instead of becoming an invisible line in `.env`.
#
# Study 29, P7.5 made the importer real: Connections › OpenAPI adds a connector from a spec, and a
# connector may read exactly these three keys besides its own (`openapi_store.RESERVED_KEY_ENVS`).
# So the labels point there again — and still say no built-in tool uses them, because none does:
# setting one alone changes nothing until a connector that names it is switched on.
_TOOL_CREDENTIALS = {
    "TAVILY_API_KEY": "Tavily (web search)",
    "BRAVE_API_KEY": "Brave — no built-in tool; an OpenAPI connector (Connections › OpenAPI) can use it",
    "SERPAPI_API_KEY": "SerpAPI — no built-in tool; an OpenAPI connector (Connections › OpenAPI) can use it",
    "ELEVENLABS_API_KEY": "ElevenLabs (TTS)",
    "STABILITY_API_KEY": "Stability — no built-in tool; an OpenAPI connector (Connections › OpenAPI) can use it",
}
# The model providers come from the catalog, which owns their slugs and their labels. Keeping a
# second list here is how the CLI and the app end up disagreeing about what a provider is called.
_PROVIDER_LABELS = {p.env: p.label for p in PROVIDERS} | _TOOL_CREDENTIALS
# Messaging bot tokens — secret (masked on read), settable so the UI can configure a channel the
# agent reaches you on without editing .env by hand.
_MESSAGING_SECRETS = {"CHIMERA_DISCORD_BOT_TOKEN", "CHIMERA_TELEGRAM_BOT_TOKEN"}
_SECRET_KEYS = set(_PROVIDER_LABELS) | {"CHIMERA_SERVER_TOKEN"} | _MESSAGING_SECRETS
_EDITABLE_SETTINGS = {
    "CHIMERA_DEFAULT_MODEL",
    "CHIMERA_WEAK_MODEL",
    "CHIMERA_MID_MODEL",
    "CHIMERA_ORCHESTRATOR_MODEL",
    "CHIMERA_COST_MODE",
    "CHIMERA_CASCADE",
    "CHIMERA_API_BASE",
    "CHIMERA_FALLBACK_MODELS",
    # Only reachable by hand-editing .env until now. `api_base` is next to it on the screen and is
    # NOT a substitute: that one is sent on every call, this one only points the Ollama provider.
    "CHIMERA_OLLAMA_BASE_URL",
    # The second local runtime, editable for the same reason as the first: a server on another port
    # or another machine should not need a hand-edited .env.
    "CHIMERA_LM_STUDIO_BASE_URL",
    # The model behind the semantic-memory toggle three rows below. Offering the switch and hiding
    # its dependency is how a control ends up confirming a change it did not make: recall degrades
    # to lexical on any embedder failure, without a word on this screen.
    "CHIMERA_EMBED_MODEL",
    # The model behind the editor's inline completion. It needs a BASE tag (an instruct model
    # ignores the suffix and answers in prose), which is a thing nobody guesses — so the field has
    # to be on the screen, not in a file the user has to be told about.
    "CHIMERA_COMPLETE_MODEL",
    "CHIMERA_VOICE_MODEL",  # the model that answers spoken TALK; empty = the conversation's
    "CHIMERA_VOICE_WORK_MODEL",  # the model that does spoken WORK; empty = the conversation's
    "CHIMERA_CACHE",
    "CHIMERA_PROMPT_CACHE",
    "CHIMERA_MEMORY_BACKEND",
    "CHIMERA_SEMANTIC_MEMORY",
    "CHIMERA_AUTO_CONSOLIDATE",
    "CHIMERA_CHAT_MEMORY",  # the "Remember from chat" toggle (opt-in durable memory from chat)
    "CHIMERA_APP_CRON",  # run the cron daemon inside the desktop app (proactivity)
    # Whether a scheduled job's channel hears that it could not run. Beside the cron switch for the
    # same reason that one is here; read per tick, so it needs no APPLIES_WHEN entry.
    "CHIMERA_CRON_NOTIFY_FAILURES",
    # The day's dollar ceiling. It existed and braked the scheduler with no screen anywhere, so a
    # person who wanted to bound what unattended jobs spend had to find it in the source. It brakes
    # ONLY scheduled jobs (`chimera/scheduler/job_runner.py`), and the Usage screen says so on the row.
    "CHIMERA_DAILY_USD_CAP",
    # Whether the machine is held awake while there is work (`chimera/core/keep_awake.py`), and
    # whether that still holds on battery. Read on the keeper's every tick, so no APPLIES_WHEN entry.
    "CHIMERA_KEEP_AWAKE",
    "CHIMERA_KEEP_AWAKE_ON_BATTERY",
    # Archive a coding conversation nobody touched for this many days (`code_api`'s list, through
    # `conversation_state.due_for_archive`). Shipped with no row, so the only way to turn it on was
    # `.env`. Read on every look at the list, so no APPLIES_WHEN entry.
    "CHIMERA_ARCHIVE_AFTER_DAYS",
    # NOT here, by design: CHIMERA_APPROVE_VIA_CHAT. Answering a pending approval from a chat bot
    # widens who can approve to whoever holds that channel, so turning it on stays a deliberate
    # edit of `.env` by the owner — never a switch a screen (or the desktop bridge) can flip.
    # Where isolated runs check their worktrees out (`chimera/core/worktree.py`). Read at every
    # worktree creation, so no APPLIES_WHEN entry: it applies from the next isolated run.
    "CHIMERA_WORKTREE_DIR",
    # The first segment of the branches those runs make (study 29, P8.1). Read at every worktree
    # creation, so no APPLIES_WHEN entry. Checked before it is written: a value git would refuse
    # would otherwise be read as the default with a warning nobody on this screen sees.
    "CHIMERA_BRANCH_PREFIX",
    # Whether the agent has `open_pull_request` at all (study 29, P8.1). Off by default; each call
    # asks the owner whatever this says. Owner-only (`bridge_routes.OWNER_ONLY_SETTINGS`): turning
    # it on widens where the owner's code can go, so no client but the owner's own screen writes it.
    "CHIMERA_PULL_REQUESTS",
    # Whether a conversation may be shared at all, and how long a new link opens it. Both only
    # narrow, and both are read per request, so neither needs an APPLIES_WHEN entry. The bridge may
    # write neither (`bridge_routes.OWNER_ONLY_SETTINGS`): their other direction widens.
    "CHIMERA_SHARING",
    "CHIMERA_SHARE_EXPIRY_HOURS",
    "CHIMERA_APP_MESSAGING",  # auto-start messaging adapters in the desktop app at boot
    # Who may talk to each bot. Not secrets — platform ids — so they are read back in full, like the
    # egress list: a list the owner cannot read is a list they cannot correct, and the failure it
    # guards against (a bot answering strangers) was invisible precisely because nothing showed it.
    "CHIMERA_DISCORD_ALLOWED_USERS",
    "CHIMERA_TELEGRAM_ALLOWED_USERS",
    "CHIMERA_SLACK_ALLOWED_USERS",
    "CHIMERA_SIGNAL_ALLOWED_USERS",
    "CHIMERA_WHATSAPP_ALLOWED_NUMBERS",
    # Whether the Discord bot attaches the files its turn wrote (study 29, P6.3). Off; and refused
    # while the Discord allowlist above is empty (`chimera/server/attachments.py`). Owner-only: it
    # sends the owner's files to a channel, so the desktop bridge may not turn it on
    # (`bridge_routes.OWNER_ONLY_SETTINGS`).
    "CHIMERA_DISCORD_ATTACH_FILES",
    "CHIMERA_GUARD_CHAT",  # assemble the chat agent with the coding turn's denylist + taint ledger
    "CHIMERA_SANDBOX",
    "CHIMERA_SANDBOX_IMAGE",
    # The docker sandbox's network: `none` (the default) or `bridge`. It existed, was read by
    # `get_sandbox`, and had no row — so a task that needs `pip install` inside the container had
    # no way to get it but a file the app never mentions. Saved values are checked
    # (`_check_sandbox_network`): anything but the two the factory understands would be stored,
    # shown, and silently read as `none`.
    "CHIMERA_SANDBOX_NETWORK",
    # Watch the page the agent is on. The setting was written, wired and reachable only by editing
    # `.env`: `default_registry` has always passed `settings.browser_headless` to the browser tool,
    # and `PATCH /api/config` has always refused the key. So the one way to see what the agent is
    # doing on a web page was a file the app never mentions.
    "CHIMERA_BROWSER_HEADLESS",
    # Where the browser may go (study 29, P5.2). The site list only narrows; the local ports open
    # loopback on the ports named, never Chimera's own. Both values are checked before they are
    # written (`_VALUE_CHECKS`), so a typo is a refusal on the screen rather than a browser that
    # silently reads the list as matching nothing.
    "CHIMERA_BROWSER_SITES",
    "CHIMERA_BROWSER_LOCAL_PORTS",
    "CHIMERA_MCP_AUTOLOAD",
    # The learn-to-use wire. Off by default, which means the agent writes skills and never reads one
    # back — the promise of the product with the switch missing from the product.
    "CHIMERA_SKILL_CARDS",
    # How much the agent may do. Editable because "configure my right hand" is unanswerable without
    # them, and because the alternative — hand-editing .env — is what people were already doing,
    # unaided, on the three settings with the largest blast radius here.
    "CHIMERA_REACH",
    "CHIMERA_APPROVAL",
    "CHIMERA_HOST_EXEC",
    # The trust kernel's own switch, and the last of this group that was still `.env`-only. The
    # three above it decide what a run may reach; this one decides whether anything JUDGES what
    # it does. It shipped `off` on every surface, with a Security screen that reported an audit
    # log and had no control to turn on the thing writing it — so the one way to get the product's
    # advertised defence was a file the app never mentions.
    "CHIMERA_GOVERNANCE",
    # Where an approval question goes when there is nobody at a console. Editable for the same
    # reason: without it a review on an unattended surface is a refusal, and the setting that
    # changes that would be discoverable only by reading the source.
    "CHIMERA_APPROVAL_WEBHOOK",
    "CHIMERA_TOOL_DENYLIST",
    # Who plays each part in a fused turn. The engine has taken these three per instance since it
    # existed; only the wire to a user was missing, so the panel a person could actually change was
    # whichever one shipped — including the judge, whose independence from the panel is the whole
    # claim fusion makes.
    "CHIMERA_FUSION_PANEL",
    "CHIMERA_FUSION_JUDGE",
    "CHIMERA_FUSION_SYNTHESIZER",
    # Where a query-string GET is not a way out, while the run holds untrusted content. Editable for
    # the reason the others are: the alternative to naming two hosts you trust is `CHIMERA_APPROVAL=
    # allow`, which says yes to everything escalated — a setting only reachable by reading the source
    # would make the blunt answer the only discoverable one.
    "CHIMERA_EGRESS_ALLOW",
    # The Experimental group: three study-25 modules whose measurements did not recommend them, so
    # they stay off. Editable anyway, because a switch that only exists in `.env` is a choice only
    # people who read the source can make — the screen shows each one with what was measured.
    "CHIMERA_BROWSER_SITUATION",
    "CHIMERA_RESEARCH_AGENT",
    "CHIMERA_EXPLORER_CONTRACT",
    # Tools reached on demand instead of declared on every step — the built-in half and the MCP
    # half. `config.py` promised beside both that the saving is reported on your own installation,
    # and the only way to switch either was `.env`. Off: the built-in bench was inconclusive
    # (`bench/tool_defer/RESULT.md`, McNemar p = 0.125) and nothing has measured the MCP half. The
    # screen shows that, and the saving measured here (`GET /api/tools/defer-saving`), on the row.
    "CHIMERA_DEFER_TOOLS",
    "CHIMERA_MCP_DEFER",
    # A project's pack narrowing skills, MCP servers and tools (study 29, P7.6). Off until measured.
    # Read per request — the agent and the registry both read it at the turn — so it needs no
    # APPLIES_WHEN entry. The bridge may not write it (`bridge_routes.OWNER_ONLY_SETTINGS`): on,
    # it only narrows, and off is the direction that widens again.
    "CHIMERA_PROJECT_PACK",
    # The desktop bridge's two switches (`chimera/api/desktop_bridge.py`). The owner's, and only the
    # owner's: the bridge refuses to write either one on Claude's behalf, even with full control on,
    # so a client can never widen its own access.
    "CHIMERA_DESKTOP_BRIDGE",
    "CHIMERA_DESKTOP_BRIDGE_FULL",
    # The System One card: which backend answers a typed decision, and which model. Written as a
    # pair and checked as one (`_check_decision_choice`) — each is valid alone and wrong together
    # often enough (a Jev slug handed to Ollama) that the value, not only the key, is refused.
    "CHIMERA_DECISION_BACKEND",
    "CHIMERA_DECISION_MODEL",
    # Whether a grounded answer is checked by that backend (`chimera/fusion/verified.py`). Read per
    # turn, so it applies from the next question. The threshold stays in `.env`: 0.8 is the
    # registered number, and a slider would invite moving it without a measurement.
    "CHIMERA_VERIFIED_ANSWERS",
    # What an OpenRouter route may do with a prompt (study 29, P5.6). Both narrow the routes that may
    # answer, so they ship off; editable because a privacy choice only reachable in `.env` is one the
    # owner of the desktop app cannot make. Read per call by the gateway, so no APPLIES_WHEN entry.
    "CHIMERA_OPENROUTER_DATA_COLLECTION",
    "CHIMERA_OPENROUTER_ZDR",
    # Where this screen saves a key: `.env`, or the OS vault (study 29, P7.7; `api/key_vault.py`).
    # Off by default. The bridge may not write it (`bridge_routes.OWNER_ONLY_SETTINGS`): its other
    # direction sends the next key typed here back into a plain-text file. Read at every save, so
    # no APPLIES_WHEN entry.
    "CHIMERA_KEY_VAULT",
}
# The settings that turn a tool ON, which the Tools screen switches (`chimera/tools/conditional.py`).
# Named there, once, and read here, so the screen can never offer a switch this endpoint refuses.
from chimera.tools.conditional import SWITCHABLE_SETTINGS  # noqa: E402

_EDITABLE_SETTINGS |= SWITCHABLE_SETTINGS
ALLOWED_KEYS = _SECRET_KEYS | _EDITABLE_SETTINGS


def is_editable(key: str) -> bool:
    """Whether ``patch_config`` will write this env var.

    The fixed allowlist above, plus any ``<PROVIDER>_API_KEY`` the credential gate now accepts. The
    two have to agree: once :mod:`chimera.providers.discovery` lets a Groq key start the agent, a
    screen that cannot save one is a screen that tells the user their key is unsupported. The
    discovery helper is also what keeps the search and speech credentials out — they match the same
    name pattern and are not providers of models.
    """
    from chimera.providers.discovery import provider_from_env_var

    return key in ALLOWED_KEYS or provider_from_env_var(key) is not None

#: When a saved setting actually starts applying, for the ones where the answer is not "now".
#:
#: Declared here, beside the allowlist, because the answer is a property of where the value is READ
#: — not of the screen that writes it. A list maintained in the frontend would go stale the first
#: time a read moves, and it would go stale silently, which is the failure this whole field exists
#: to stop: a control that confirms and does nothing spends the user's trust in every other control
#: on the screen.
#:
#: Anything absent from this map applies to the next call. That is the common case now that the
#: gateway and the request handlers read through instead of holding a boot-time snapshot.
NEXT_CONVERSATION = "next_conversation"
NEXT_LAUNCH = "next_launch"
#: Two moments at once: what builds its sandbox per use (a `!` command, a workflow or cron shell
#: step, the verifier) takes the new value immediately, while an open conversation keeps the tools
#: it was built with. Saying only "next conversation" would describe the side that WIDENS access as
#: later than it is.
COMMANDS_NOW = "commands_now"
APPLIES_WHEN: dict[str, str] = {
    # Decided when a conversation is built (`factory()` in `chimera app`), so an open conversation
    # keeps the behaviour it started with — deliberately: changing a running chat's guard or backend
    # underneath it would make its transcript describe two different agents.
    "CHIMERA_CASCADE": NEXT_CONVERSATION,
    "CHIMERA_GUARD_CHAT": NEXT_CONVERSATION,
    "CHIMERA_CHAT_MEMORY": NEXT_CONVERSATION,
    # Read once, when `default_registry` constructs the browser tool — and the tool then keeps the
    # Chromium it launched for as long as it lives. Re-reading the value could not pull a window
    # onto the screen of a browser that is already running headless, so the honest answer is the
    # next conversation, which is when a fresh registry (and a fresh browser) is built.
    "CHIMERA_BROWSER_HEADLESS": NEXT_CONVERSATION,
    # Read at the same point as the headless switch: `default_registry` hands the browser its reach
    # when it builds the tool, and the reach then holds for that browser's life.
    "CHIMERA_BROWSER_SITES": NEXT_CONVERSATION,
    "CHIMERA_BROWSER_LOCAL_PORTS": NEXT_CONVERSATION,
    # Same read point: `default_registry` hands the browser its situation when it builds the tool, and
    # the loop's config takes the flag when the agent is built. A chat keeps both for its lifetime;
    # a Code turn builds both afresh, so there it is the next turn. The research agent and the
    # explorer's contract are read only on the Code turn, per turn, so they are absent: next call.
    "CHIMERA_BROWSER_SITUATION": NEXT_CONVERSATION,
    # Both read where the registry is assembled: per conversation in the chat (`_chat_session` reads
    # `get_settings()` fresh) and per turn on the Code screen, which is sooner. "Next conversation"
    # is the scope that is true on both; an open chat keeps the tool list it started with.
    "CHIMERA_DEFER_TOOLS": NEXT_CONVERSATION,
    # `default_registry` decides whether to build the tool: per conversation in the chat, per turn on
    # the Code screen, per job on cron. "Next conversation" is the scope true on all of them.
    "CHIMERA_PULL_REQUESTS": NEXT_CONVERSATION,
    "CHIMERA_MCP_DEFER": NEXT_CONVERSATION,
    # Read at two points. `default_registry` builds the chat's shell and code tools, each with its
    # own sandbox object (`get_sandbox()` in `chimera/tools/builtin.py`), so an open chat keeps the
    # network its tools were built with; a Code turn builds them afresh. But three callers build the
    # sandbox on every use and so take a save at once: the user's `!` command
    # (`api/exec_stream.py`), a workflow or cron shell step (`workflow/executors.py`, unattended),
    # and the verifier (`core/verify.py`). Hence COMMANDS_NOW, not NEXT_CONVERSATION.
    "CHIMERA_SANDBOX_NETWORK": COMMANDS_NOW,
    # The governance band builds its decider once per assembly (`governance/band.py::build_band`), so
    # a chat already running keeps the instrument it started with; the next one reads the new pair.
    # `POST /api/decide` and the `decide` tool rebuild on the next call.
    "CHIMERA_DECISION_BACKEND": NEXT_CONVERSATION,
    "CHIMERA_DECISION_MODEL": NEXT_CONVERSATION,
    # These start something at boot — a daemon thread and a set of MCP subprocesses. Re-reading the
    # value would not undo that, so the honest answer is the relaunch, not a re-read.
    "CHIMERA_APP_CRON": NEXT_LAUNCH,
    "CHIMERA_MCP_AUTOLOAD": NEXT_LAUNCH,
    # Read when the bot is built — at `chimera serve` start, or by the app's messaging manager from
    # the settings it was launched with — and the running adapter keeps the set it was handed.
    "CHIMERA_DISCORD_ALLOWED_USERS": NEXT_LAUNCH,
    "CHIMERA_TELEGRAM_ALLOWED_USERS": NEXT_LAUNCH,
    "CHIMERA_SLACK_ALLOWED_USERS": NEXT_LAUNCH,
    "CHIMERA_SIGNAL_ALLOWED_USERS": NEXT_LAUNCH,
    "CHIMERA_WHATSAPP_ALLOWED_NUMBERS": NEXT_LAUNCH,
    # Read at the same point: the adapter is built with it, and the gateway's hook with the adapter.
    "CHIMERA_DISCORD_ATTACH_FILES": NEXT_LAUNCH,
}


def _browser_reach_lists(settings: Settings) -> dict[str, Any]:
    """The two lists as the browser reads them, and — when either does not parse — why.

    A value that does not parse is not "empty": `default_registry` leaves the browser out of the
    registry altogether (`BrowserReach.from_settings` raises), so empty lists alone would show the
    owner "any public site" for a browser that exists in no conversation. ``invalid`` carries the
    parser's own message, which names the key and the entry."""
    from chimera.tools.browser_reach import parse_ports, parse_sites

    errors: list[str] = []
    try:
        sites = list(parse_sites(settings.browser_sites))
    except ValueError as exc:
        sites, errors = [], [*errors, str(exc)]
    try:
        ports = sorted(parse_ports(settings.browser_local_ports))
    except ValueError as exc:
        ports, errors = [], [*errors, str(exc)]
    return {"sites": sites, "local_ports": ports, "invalid": "; ".join(errors) or None}


def _fusion_kinship(panel: list[str], judge: str) -> dict[str, Any]:
    """How independent the judge is from the panel it grades — the engine's own answer.

    Delegated to ``FusionConfig.role_kinship`` rather than reimplemented, because the interesting
    half of it is the one nobody reimplements: not "is the judge a panelist" (obvious) but "is the
    judge from the same lab as one" — which is not the same model and is not a second independent
    vote either. Two copies of that rule would drift, and the copy on screen would be the one people
    believe.
    """
    from chimera.fusion.engine import FusionConfig

    return dict(FusionConfig(panel=list(panel), judge=judge, synthesizer=judge).role_kinship())


def _hint(value: str | None) -> str:
    """A safe recognition hint: the last 4 chars of a long secret, else empty. Never the whole value."""
    if not value or len(value) < 8:
        return ""
    return f"…{value[-4:]}"


def _pool_env(provider: str) -> str:
    """``"openrouter"`` -> ``CHIMERA_OPENROUTER_KEYS``, or ``ValueError`` for anything else.

    Only the five with a settings field have a pool: rotation and cooldown are per-provider state
    the gateway keeps, not something a name alone can conjure. A discovered provider gets its single
    key from the environment and is none the worse for it.
    """
    from chimera.providers.catalog import PROVIDERS_BY_NAME, provider_names

    if provider not in PROVIDERS_BY_NAME:
        raise ValueError(f"unknown provider: {provider} (known: {', '.join(provider_names())})")
    return f"CHIMERA_{provider.upper()}_KEYS"


def read_pools(settings: Settings) -> list[dict[str, Any]]:
    """Every provider's rotation pool, masked — position and last four characters, nothing else."""
    from chimera.providers.catalog import PROVIDERS_BY_NAME

    return [
        {
            "provider": name,
            "env": _pool_env(name),
            "keys": [
                {"index": i, "hint": _hint(key)}
                for i, key in enumerate(settings.credential_pool(name))
            ],
        }
        for name in PROVIDERS_BY_NAME
    ]


def _write_pool(provider: str, keys: list[str], env_path: Path | None) -> dict[str, Any]:
    env = _pool_env(provider)
    value = ",".join(keys)
    path = env_path or Path(".env")
    # A pool is a list of keys, so it follows the vault switch like any single key does — a toggle
    # that kept the rotation's keys in the file would be a promise about "the keys" that holds for
    # some of them. The pool variables are vault-storable for exactly this.
    in_vault, fallback = write_credentials({env: value}, path=path, vault_on=get_settings().key_vault)
    if env not in SCREEN_STORABLE:
        _write_env_var(path, env, value)
    os.environ[env] = value
    get_settings.cache_clear()
    result: dict[str, Any] = {"provider": provider, "count": len(keys)}
    # Passed on, as `patch_config` does, and only when there is something to say. A pool written to
    # `.env` with the switch on (no vault on this machine) is the state the screen must never show
    # in silence - the pool card reads `vault_fallback` and says so.
    if in_vault:
        result["in_vault"] = in_vault
    if fallback:
        result["vault_fallback"] = fallback
    return result


def pool_add(provider: str, key: str, *, env_path: Path | None = None) -> dict[str, Any]:
    """Append one key to a provider's pool.

    The client sends a key and never a list, which is what makes the read-modify-write safe: the
    server holds the only copy of the other keys, so a stale or masked client view cannot destroy
    them. Rejects a comma (it is the pool separator, so one key would silently become two) and
    anything shaped like the mask this API hands out.
    """
    candidate = (key or "").strip()
    if not candidate:
        raise ValueError("key may not be empty")
    if "," in candidate:
        raise ValueError("key may not contain a comma — that is the separator between pool entries")
    if any(c in candidate for c in "\r\n"):
        raise ValueError("key may not contain a newline")
    if candidate.startswith("…") or set(candidate) <= {"*", "•", "·"}:
        # A client echoing back what it displayed. Cheap to check, and it fails loudly here instead
        # of quietly replacing a working pool with its own mask.
        raise ValueError("that looks like a masked hint, not a key")
    existing = list(get_settings().credential_pool(provider)) if provider else []
    if candidate in existing:
        raise ValueError("that key is already in the pool")
    return _write_pool(provider, [*existing, candidate], env_path)


def pool_remove(provider: str, index: int, *, env_path: Path | None = None) -> dict[str, Any]:
    """Drop the key at ``index``. An index, never a value — the client has never seen one."""
    existing = list(get_settings().credential_pool(provider)) if provider else []
    _pool_env(provider)  # validates the provider even when the pool is empty
    if not 0 <= index < len(existing):
        raise ValueError(f"no key at index {index} (pool has {len(existing)})")
    return _write_pool(provider, existing[:index] + existing[index + 1 :], env_path)


def read_config(settings: Settings, *, env_path: Path | None = None) -> dict[str, Any]:
    """The settings snapshot for the UI. Secrets are masked to ``{set, hint}`` — never cleartext."""
    creds = settings.credentials()
    vault = vault_snapshot(enabled=settings.key_vault, path=env_path or Path(".env"))
    in_vault = set(vault["keys"])
    known = {p.env: p for p in PROVIDERS}
    providers = [
        {
            "env": env,
            # The provider's routing name, derived here rather than in the client. It is the env var
            # minus the suffix, lowercased — the same rule `discovery.provider_from_env_var` applies
            # — and a second copy of that rule in TypeScript is how a screen ends up asking about
            # `OPENROUTER` while the gateway is routing `openrouter`.
            "name": env.removesuffix("_API_KEY").lower(),
            "label": _PROVIDER_LABELS[env],
            "set": bool(creds.get(env)),
            "hint": _hint(creds.get(env)),
            "llm": env in known,
            "model": known[env].default_model if env in known else "",
            "keys_url": known[env].keys_url if env in known else "",
            "in_vault": env in in_vault,
        }
        for env in _PROVIDER_LABELS
    ]
    # Providers Chimera was never told about. A key for any of LiteLLM's other ~100 vendors now opens
    # the gate, so the screen listing credentials has to be able to show one — otherwise the app
    # reports "no key" about a key it is currently using. They arrive without a suggested model or a
    # sign-up page, which is the honest answer: we discovered the credential, we did not ship support
    # for the vendor. The value is masked by the same `_hint`.
    from chimera.providers.discovery import generic_providers

    for name in generic_providers():
        env = f"{name.upper()}_API_KEY"
        value = os.environ.get(env)
        providers.append(
            {
                "env": env,
                "name": name,
                "label": name.title(),
                "set": bool(value),
                "hint": _hint(value),
                "llm": True,
                "model": "",
                "keys_url": "",
            }
        )
    ladder = settings.tier_ladder()
    pools = read_pools(settings)
    from chimera.sandbox import sandbox_network

    # Imported here: `chimera.server` pulls in every adapter and the HTTP server, which a settings
    # read has no other reason to load.
    from chimera.server.allowlist import ALLOWLIST_FIELDS, allowed_ids, bot_configured

    return {
        "models": {
            "default": settings.default_model,
            "weak": settings.weak_model,
            "mid": settings.mid_model,
            "orchestrator": settings.orchestrator_model,
            "cost_mode": settings.cost_mode,
            "cascade": settings.cascade,
            "api_base": settings.api_base,
            "fallback_models": list(settings.fallback_models),
            "tiers": {"weak": ladder.weak, "mid": ladder.mid, "top": ladder.top},
            "ollama_base_url": settings.ollama_base_url,
            "lm_studio_base_url": settings.lm_studio_base_url,
            "complete_model": settings.complete_model,
            "voice_model": settings.voice_model,
            "voice_work_model": settings.voice_work_model,
        },
        # Panel -> judge -> synthesizer, and how independent the judge actually is from the panel it
        # grades. `role_kinship` is reported rather than enforced: a user with one provider key has
        # no way to avoid overlap, and a labelled receipt beats a refusal they cannot act on.
        "fusion": {
            "panel": list(settings.fusion_panel),
            "judge": settings.fusion_judge,
            "synthesizer": settings.fusion_synthesizer,
            "mode": settings.fusion_mode,
            "kinship": _fusion_kinship(
                list(settings.fusion_panel), settings.fusion_judge
            ),
        },
        "memory": {
            # Resolved, so the screen names the store the turns actually read — `sqlite` on the
            # default, `json` when the owner chose it or the build has no FTS5.
            "backend": resolve_memory_backend(settings),
            "semantic": settings.semantic_memory,
            "auto_consolidate": settings.auto_consolidate,
            "remember_from_chat": settings.remember_from_chat,
            "skill_cards": settings.skill_cards,
            "embed_model": settings.embed_model,
        },
        "cache": {"completion": settings.cache, "prompt": settings.prompt_cache},
        "sandbox": {
            "mode": settings.sandbox,
            "image": settings.sandbox_image,
            # As the factory reads it, not as typed: `get_sandbox` opens the bridge for `bridge` and
            # for nothing else, so a hand-edited `.env` holding anything else is `none` in fact and
            # must not be shown as something else on the row that edits it.
            "network": sandbox_network(settings),
            # The one exception to that network, read where it acts (`core/verify.py`): the verifier
            # rebuilds a container with the network on, and under a kernel sandbox runs a command
            # the user typed on the host. A row reading "no network" has to be able to say so.
            "verify_network": settings.verify_network,
        },
        "browser": {
            "headless": settings.browser_headless,
            **_browser_reach_lists(settings),
        },
        "experimental": {
            "browser_situation": settings.browser_situation,
            "research_agent": settings.research_agent,
            "explorer_contract": settings.explorer_contract,
        },
        # Tools on demand, both halves. What they would save is `GET /api/tools/defer-saving`.
        "defer": {"tools": settings.defer_tools, "mcp": settings.mcp_defer},
        # Whether a project's `.chimera/pack.json` may narrow a run. What one pack does is
        # `GET /api/code/pack`, per folder.
        "project_pack": {"enabled": settings.project_pack},
        # The day's dollar ceiling, as set; `None` is no cap. Scheduled jobs only — see SpendCfgOut.
        "spend": {"daily_usd_cap": settings.daily_usd_cap},
        # The owner's keep-awake choice. What the keeper is DOING is `GET /api/keep-awake`.
        "keep_awake": {
            "mode": settings.keep_awake,
            "on_battery": settings.keep_awake_on_battery,
        },
        # As set; empty is the system temp folder. Where the next worktree actually goes (after the
        # rules that can refuse a value) is `GET /api/storage`'s `worktree_dir`.
        "storage": {"worktree_dir": settings.worktree_dir, "branch_prefix": settings.branch_prefix},
        # The two settings that narrow sharing. Which links exist is `GET /api/security/access`.
        "sharing": {
            "enabled": settings.sharing,
            "expiry_hours": settings.share_expiry_hours,
        },
        # Which backend answers a typed decision, and which model; empty = the backend's default.
        "decisions": {
            "backend": (settings.decision_backend or "local_logprob").strip(),
            "model": (settings.decision_model or "").strip(),
            # Grounded answers checked by it (study 26); on unless the owner turned it off.
            "verified_answers": bool(settings.verified_answers),
            "verified_answers_threshold": float(settings.verified_answers_threshold),
        },
        # Whether Claude may operate this app, and whether it may also answer approvals and edit
        # settings. Applied live: saving either one writes or removes the discovery file at once.
        "bridge": {
            "enabled": settings.desktop_bridge,
            "full": settings.desktop_bridge_full,
        },
        "autonomy": {
            "reach": settings.reach,
            "approval": settings.approval,
            "host_exec": settings.host_exec,
            "denied_tools": list(settings.tool_denylist),
            # The kernel's own switch, beside the three settings that say what a run may reach.
            # It decides whether anything judges what a run DOES, and it was the only one of the
            # four with no way to change it but a text editor.
            "governance": settings.governance_mode,
            # Reported as a fact about configuration, never as the value: the URL is a credential,
            # and whoever holds it can post into that channel. Same shape as `server.token_set`.
            "approval_webhook_set": bool(settings.approval_webhook.strip()),
            # Whether the agent may propose a pull request at all. Every proposal asks the owner.
            "pull_requests": settings.pull_requests,
            # The destinations the owner declared as not-a-way-out. A plain list, not a secret:
            # it is a statement the owner made and has to be able to read back, and a row that
            # cannot show what it holds is a row nobody can correct.
            "egress_allow": [
                host.strip() for host in settings.egress_allow.split(",") if host.strip()
            ],
        },
        # ON by default since 2026-09-10 — see Settings.guard_chat. Exposed here because the posture
        # line points at this switch by name when it reports a conversation as unguarded, which is
        # now the state an owner has to have chosen rather than the one they were given.
        "guard": {"chat": settings.guard_chat},
        "server": {"token_set": bool(settings.server_token)},
        "mcp": {"autoload": settings.mcp_autoload},
        "automation": {
            "cron": settings.app_cron,
            # Whether a job's channel hears that the job could not run. On by default.
            "notify_failures": settings.cron_notify_failures,
        },
        # `None` is never, the shipped state.
        "conversations": {"archive_after_days": settings.archive_after_days},
        # Per platform, the ids allowed to talk to its bot; an empty list is "anyone", and the
        # Messaging card says so in those words rather than showing a blank field.
        "messaging": {
            "allowed_users": {
                platform: allowed_ids(settings, platform) for platform in ALLOWLIST_FIELDS
            },
            # The platforms whose bot has what it needs to start, so an empty list can be read as
            # "anyone" only where a bot exists. Booleans, never the tokens.
            "configured": [
                platform for platform in ALLOWLIST_FIELDS if bot_configured(settings, platform)
            ],
            # The switch as saved. Whether it ACTS also needs the Discord allowlist, and the card
            # says so beside the switch rather than letting an "on" read as files being sent.
            "discord_attach_files": settings.discord_attach_files,
        },
        # Who receives a prompt and what the OpenRouter route may keep — the Security screen's
        # privacy card. See `chimera/providers/privacy.py`.
        "privacy": privacy_snapshot(settings),
        "providers": providers,
        # Whether this screen saves keys into the OS vault, whether there is one, and which keys
        # (names) it holds — `api/key_vault.py`. Read before `providers` is built, so each row can
        # carry its own badge.
        "vault": vault,
        "pools": pools,
        # Keys absent here apply to the next call; see APPLIES_WHEN.
        "applies": dict(APPLIES_WHEN),
        # Which of those keys this deployment's environment pins — see `pinned_by_environment`.
        # Every writable key is offered, secrets included: an `OPENROUTER_API_KEY` exported by the
        # unit file reverts a key pasted here exactly the same way a `CHIMERA_REACH` does, and it is
        # the one this screen is least likely to be believed about. The pool variables are in the
        # set for the same reason and are NOT in `ALLOWED_KEYS`: they are written by the pool
        # endpoints rather than by `patch_config`, through the same `.env` and with the same fate.
        "pinned": pinned_by_environment(ALLOWED_KEYS | {str(p["env"]) for p in pools}),
    }


def doctor(settings: Settings) -> dict[str, Any]:
    """A config-health snapshot (no live provider pings): which providers have keys, the model ladder."""
    from chimera.acp.agents import available_agents
    from chimera.providers.discovery import is_local_model
    from chimera.tools.code import host_python_report

    ladder = settings.tier_ladder()
    return {
        "has_any_key": settings.has_any_key(),
        "local_model": is_local_model(settings.default_model),
        "can_answer": settings.can_answer(),
        "configured_providers": settings.configured_providers(),
        "default_model": settings.default_model,
        "tiers": {"weak": ladder.weak, "mid": ladder.mid, "top": ladder.top},
        "memory_backend": resolve_memory_backend(settings),
        "cache": settings.cache,
        "sandbox": settings.sandbox,
        # Capability by capability, measured on THIS machine. A frozen sidecar is built by CI on a
        # machine nobody looked at, so "the adapter should be there" stops being evidence at exactly
        # the point a user needs the answer — and `npx` missing reads identically to a bug in us.
        "external_agents": available_agents(),
        # Whether a spend cap could even work here — see pricing_capability.
        "spend": pricing_capability(settings),
        # The editor's own capabilities, measured on THIS machine. Same reason as the agents above:
        # a downloaded app is the exact place where "it should be installed" stops being evidence,
        # and the answer a new user needs is "what do I install", not "something is unavailable".
        "editor": editor_capabilities(settings),
        # Which Python `execute_code` runs on THIS machine. In the frozen desktop build there is no
        # interpreter of its own, so it is whatever PATH holds, or none — and a snippet that cannot
        # start reads in a transcript like a model that wrote bad code. Saying which one is what
        # tells the two apart.
        "code_python": host_python_report(),
    }


def pricing_capability(settings: Settings) -> dict[str, object]:
    """Whether this machine's default model can be priced at all.

    A dollar cap stops the run when it meets a call it cannot price — the safe rule, and the one
    that turns an unpriced default model into a feature that refuses to work. The moment to learn
    that is while reading `doctor`, not when a 3 a.m. cron job halts. So the answer is reported
    before anyone sets a cap, with the model named.
    """
    from chimera.fusion.receipts import resolve_price

    # Every model that could ANSWER a turn on this machine, not just the one it would ask. The Fuse
    # button sits in the same composer row as the ceiling and always works, so a panel member with
    # no list price is exactly as capable of making the total unknowable as the default model is —
    # and probing only the default reported "priced" for the most expensive turn the app can run.
    model = settings.default_model
    cast = [m for m in (*settings.fusion_panel, settings.fusion_judge, settings.fusion_synthesizer) if m]
    unpriced = [m for m in dict.fromkeys([model, *cast]) if m and resolve_price(m) is None]
    priced = not unpriced
    named = ", ".join(unpriced)
    return {
        "key": "spend_cap",
        "label": "Spend cap (dollar ceiling)",
        "available": priced,
        "probed": True,  # the price table either resolves these models or it does not
        "detail": model,
        "hint": (
            ""
            if priced
            else f"no list price known for {named}: a spend cap would stop on the first call that "
            "reaches one. Register a price with chimera.fusion.receipts.set_price, or run with "
            "models that have one."
        ),
    }


def editor_capabilities(settings: Settings) -> list[dict[str, object]]:
    """Diagnostics and inline completion: present or absent, with the command that fixes absent.

    The two are known to DIFFERENT degrees, and `probed` is what says so. `ruff` is a program, so
    resolving it is a real answer. The completion model lives behind a server that may be on another
    machine; pinging it would make `doctor` slow and occasionally wrong about a machine that is
    merely asleep, so all that is known there is that a model and a URL were configured.

    Collapsing the two into one word would be the lie this whole surface exists to avoid: "available"
    for a completion model nobody has reached is a promise the editor then quietly fails to keep.
    """
    from chimera.api.lsp_api import ruff_available

    return [
        {
            "key": "diagnostics",
            "label": "Editor diagnostics (ruff)",
            "available": ruff_available(),
            "probed": True,  # the program either resolves on this machine or it does not
            "detail": "ruff server",
            "hint": "pip install ruff (or install Chimera's 'dev' extra)",
        },
        {
            "key": "completion",
            "label": "Inline completion (local model)",
            # CONFIGURED, not reached. The editor reports the live answer, because it is the only
            # surface that has just asked one.
            "available": bool(settings.complete_model and settings.ollama_base_url),
            "probed": False,
            "detail": f"{settings.complete_model or '(unset)'} at {settings.ollama_base_url or '(unset)'}",
            "hint": f"ollama pull {settings.complete_model}" if settings.complete_model else "set CHIMERA_COMPLETE_MODEL",
        },
    ]


def _write_env_var(path: Path, key: str, value: str) -> None:
    """Set ``KEY=value`` in ``.env`` atomically.

    Through `key_vault.set_env_entry`, which replaces EVERY entry for the key — duplicate
    assignments (the last one wins when the file is read, so replacing only the first left the old
    value in force) and a vault marker (which would otherwise sit above the key it says is elsewhere).
    """
    write_env_value(path, key, value)


def _check_daily_cap(value: str) -> None:
    """Empty (no cap) or a positive dollar amount.

    Zero is refused rather than stored: the scheduler reads the cap with ``if cap``, so ``0`` would
    be saved, shown as a cap of $0.00, and brake nothing. A value that does not parse is refused
    because ``Settings`` would then fail to build and take the whole app down at the next read.
    """
    text = value.strip()
    if not text:
        return
    try:
        amount = float(text)
    except ValueError as exc:
        raise ValueError(f"CHIMERA_DAILY_USD_CAP must be a dollar amount, not {text!r}") from exc
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError(
            "CHIMERA_DAILY_USD_CAP must be more than zero; leave it empty for no daily cap"
        )


def _check_share_expiry(value: str) -> None:
    """Empty (never) or a positive number of hours. Zero and negatives are refused rather than read
    as "never", for the reason `_check_daily_cap` gives: saved, they would look like a choice and
    mean its opposite."""
    text = value.strip()
    if not text:
        return
    try:
        hours = float(text)
    except ValueError as exc:
        raise ValueError(f"CHIMERA_SHARE_EXPIRY_HOURS must be a number of hours, not {text!r}") from exc
    if not math.isfinite(hours) or hours <= 0:
        raise ValueError(
            "CHIMERA_SHARE_EXPIRY_HOURS must be more than zero; leave it empty for links that never expire"
        )


def _check_keep_awake(value: str) -> None:
    if value.strip().lower() not in ("off", "working", "always"):
        raise ValueError("CHIMERA_KEEP_AWAKE must be one of off, working, always")


def _check_archive_after_days(value: str) -> None:
    """Empty (never) or a positive number of days.

    Zero is refused for the reason the daily cap refuses it: the rule reads ``if not after_days``,
    so ``0`` would be saved, shown as a number, and archive nothing. A value that does not parse is
    refused here even though ``Settings`` would survive it (it falls back to never with a warning in
    a log): a screen that confirms a save which then does nothing is the failure this check exists
    to stop.
    """
    text = value.strip()
    if not text:
        return
    try:
        days = float(text)
    except ValueError as exc:
        raise ValueError(f"CHIMERA_ARCHIVE_AFTER_DAYS must be a number of days, not {text!r}") from exc
    if not math.isfinite(days) or days <= 0:
        raise ValueError(
            "CHIMERA_ARCHIVE_AFTER_DAYS must be more than zero; leave it empty to never archive"
        )


def _check_browser_sites(value: str) -> None:
    from chimera.tools.browser_reach import parse_sites

    parse_sites(value)


def _check_browser_ports(value: str) -> None:
    from chimera.tools.browser_reach import parse_ports

    parse_ports(value)


def _check_worktree_dir(value: str, workspace: Path | None = None) -> None:
    """Empty (temp) or an absolute path outside the project. Refused here, at the save, rather than
    ignored with a warning in a log the owner never reads.

    A relative path would resolve against wherever the backend happened to start, which for a
    packaged app is the install folder. A folder inside the workspace is passed over for temp by
    `GitWorktree.create` — the project's own status, search and checkpoints would read the run's
    checkout — so saving it would be a setting that silently does nothing for that project."""
    text = value.strip()
    if text and not Path(text).expanduser().is_absolute():
        raise ValueError("CHIMERA_WORKTREE_DIR must be an absolute path, or empty for the temp folder")
    if text and workspace is not None:
        from chimera.core.worktree import is_inside

        if is_inside(Path(text).expanduser(), workspace):
            raise ValueError(
                f"CHIMERA_WORKTREE_DIR may not be inside the project ({workspace}); "
                "a worktree there would be read as part of it"
            )


def _check_sandbox_network(value: str) -> None:
    """``none`` or ``bridge``, and nothing else.

    ``get_sandbox`` reads anything but ``bridge`` as ``none``, so a stray value would not open the
    network — it would be saved, shown on the row as if it meant something, and do nothing. And the
    one docker value it would be natural to try, ``host``, is the one that must never be accepted:
    it shares this machine's network stack with the container, which is no boundary at all.
    """
    if value.strip().lower() not in ("none", "bridge"):
        raise ValueError("CHIMERA_SANDBOX_NETWORK must be none or bridge")


def _check_data_collection(value: str) -> None:
    # Refused here rather than read as `deny` by the settings validator: that fallback exists for a
    # hand-edited `.env`, and a screen that offers two words has no business saving a third.
    if value.strip().lower() not in ("allow", "deny"):
        raise ValueError("CHIMERA_OPENROUTER_DATA_COLLECTION must be allow or deny")


#: How the settings validator reads a boolean as on. Needed here, before the save, to decide where
#: the keys in the same patch go.
_ON_WORDS = ("true", "1", "yes", "on")


def _check_boolean(key: str) -> Callable[[str], None]:
    def check(value: str) -> None:
        if value.strip().lower() not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
            raise ValueError(f"{key} must be true or false")

    return check


def _check_branch_prefix(value: str) -> None:
    """One ref segment, or empty for the default — the shape the setting's validator accepts.

    Refused here rather than left to the validator, which reads a bad value as `chimera` and logs
    it: a save that the screen reports as done while the branches keep their old name is a control
    that confirms a change it did not make.
    """
    from chimera.config import is_branch_prefix

    word = value.strip()
    if word and not is_branch_prefix(word):
        raise ValueError(
            "CHIMERA_BRANCH_PREFIX must be one word of letters, digits, '-' or '_' "
            "(at most 40), not a name Windows reserves (CON, PRN, AUX, NUL, COM0-9, LPT0-9), "
            "or empty for 'chimera'"
        )


#: Values checked before anything is written, for the keys where a bad value is worse than a
#: refusal: one the app would fail to start on, or one that would be saved and silently do nothing.
_VALUE_CHECKS: dict[str, Callable[[str], None]] = {
    "CHIMERA_DAILY_USD_CAP": _check_daily_cap,
    "CHIMERA_KEEP_AWAKE": _check_keep_awake,
    "CHIMERA_BROWSER_SITES": _check_browser_sites,
    "CHIMERA_BROWSER_LOCAL_PORTS": _check_browser_ports,
    "CHIMERA_KEEP_AWAKE_ON_BATTERY": _check_boolean("CHIMERA_KEEP_AWAKE_ON_BATTERY"),
    "CHIMERA_ARCHIVE_AFTER_DAYS": _check_archive_after_days,
    # A boolean the app would fail to start on if it were saved as anything else.
    "CHIMERA_CRON_NOTIFY_FAILURES": _check_boolean("CHIMERA_CRON_NOTIFY_FAILURES"),
    # A value `Settings` cannot parse as a bool takes the whole app down at the next read, which is
    # worse than a refusal here.
    "CHIMERA_DEFER_TOOLS": _check_boolean("CHIMERA_DEFER_TOOLS"),
    "CHIMERA_MCP_DEFER": _check_boolean("CHIMERA_MCP_DEFER"),
    "CHIMERA_PROJECT_PACK": _check_boolean("CHIMERA_PROJECT_PACK"),
    # CHIMERA_WORKTREE_DIR is checked in `patch_config` itself: its check needs the workspace.
    "CHIMERA_SANDBOX_NETWORK": _check_sandbox_network,
    "CHIMERA_SHARING": _check_boolean("CHIMERA_SHARING"),
    "CHIMERA_SHARE_EXPIRY_HOURS": _check_share_expiry,
    "CHIMERA_OPENROUTER_DATA_COLLECTION": _check_data_collection,
    "CHIMERA_OPENROUTER_ZDR": _check_boolean("CHIMERA_OPENROUTER_ZDR"),
    "CHIMERA_KEY_VAULT": _check_boolean("CHIMERA_KEY_VAULT"),
    "CHIMERA_PULL_REQUESTS": _check_boolean("CHIMERA_PULL_REQUESTS"),
    "CHIMERA_BRANCH_PREFIX": _check_branch_prefix,
}


def _check_decision_choice(updates: dict[str, str]) -> None:
    """Refuse a decision backend/model pair the factory cannot honour, before anything is written.

    The pair is checked as it will stand AFTER the save: a patch that names only one of the two is
    read against the other's current value, because that is the pair ``build_backend`` will receive.
    The listing is fetched only when a model is named — an empty model is every backend's measured
    default and needs no index.
    """
    if not {"CHIMERA_DECISION_BACKEND", "CHIMERA_DECISION_MODEL"} & set(updates):
        return
    from chimera.decisions.system_one import SystemOneListing, check_choice, list_models

    current = get_settings()
    backend = str(updates.get("CHIMERA_DECISION_BACKEND", current.decision_backend or "local_logprob"))
    model = str(updates.get("CHIMERA_DECISION_MODEL", current.decision_model or ""))
    check_choice(backend, model, list_models() if model.strip() else SystemOneListing(models=()))


def check_updates(updates: dict[str, str], *, workspace: Path | None = None) -> None:
    """Every refusal ``patch_config`` makes before it writes, without writing. ``ValueError`` names it.

    Its own function so a write that happens LATER than the request — a settings suggestion the
    owner approves a day after it was made (`governance/setting_suggestions.py`) — is held to the
    same checks at both moments, by the same code.
    """
    rejected = [k for k in updates if not is_editable(k)]
    if rejected:
        raise ValueError(f"not editable: {', '.join(sorted(rejected))}")
    # Allowlisting the KEY isn't enough: a newline in the VALUE would split into extra .env lines and
    # inject arbitrary env vars (e.g. a provider key, or disabling the sandbox). Reject control chars.
    for key, value in updates.items():
        if any(c in str(value) for c in "\r\n"):
            raise ValueError(f"value for {key} may not contain a newline")
    for key, value in updates.items():
        check = _VALUE_CHECKS.get(key)
        if check is not None:
            check(str(value))
    if "CHIMERA_WORKTREE_DIR" in updates:
        # The one check that needs to know which project the backend serves; `workspace` is the
        # API's, and a caller without one (the CLI) gets the path check above only.
        _check_worktree_dir(str(updates["CHIMERA_WORKTREE_DIR"]), workspace)
    _check_decision_choice(updates)


def check_parses(updates: dict[str, str]) -> None:
    """Refuse a value ``Settings`` could not read back — ``CHIMERA_CASCADE=maybe``.

    The owner's own save does not ask this (a typo there is the owner's, made on the screen that
    shows it). A suggestion is someone else's value, applied by a click on a card, and a boolean
    that does not parse takes the whole app down at its next read — so it is refused before the
    card exists and again before it is applied. Only errors located AT one of the keys count: a
    cross-field rule evaluated against defaults would refuse for reasons the real settings do not
    have.
    """
    from pydantic import ValidationError

    try:
        Settings.model_validate(dict(updates))
    except ValidationError as exc:
        wrong = sorted(
            {
                str(err["loc"][0])
                for err in exc.errors()
                if err.get("loc") and str(err["loc"][0]).upper() in {k.upper() for k in updates}
            }
        )
        if wrong:
            raise ValueError(f"not a valid value for {', '.join(wrong)}") from None


def setting_value(settings: Settings, key: str) -> str:
    """The value ``key`` holds in ``settings``, written the way ``.env`` would hold it.

    What a settings suggestion shows as "now" and compares at apply time, so both readings go
    through one function: a list is comma-joined (the documented form), a boolean is
    ``true``/``false``, an unset value is "". ``ValueError`` for a key no field reads.
    """
    for name, field in type(settings).model_fields.items():
        if str(field.validation_alias or "").upper() == key.upper():
            value = getattr(settings, name)
            break
    else:
        raise ValueError(f"no setting reads {key}")
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)


def patch_config(
    updates: dict[str, str], *, env_path: Path | None = None, workspace: Path | None = None
) -> dict[str, Any]:
    """Persist ``updates`` (env-var -> value) to ``.env`` after allowlisting the keys.

    Returns ``{"updated": [keys]}``, plus ``in_vault`` / ``vault_fallback`` when not empty. Raises
    ``ValueError`` naming any rejected key (so the endpoint can 400 it). Clears the ``get_settings``
    cache so the next read sees the new values. Values are written verbatim and never logged.

    With ``CHIMERA_KEY_VAULT`` on — as it stands AFTER this save, so a patch that turns it on and
    sets a key does both — a vault-storable credential goes to the OS vault and its ``.env`` line
    becomes a marker (``in_vault``); with no vault on the machine it goes to ``.env`` and is named
    in ``vault_fallback``. See `chimera/api/key_vault.py`.
    """
    check_updates(updates, workspace=workspace)
    path = env_path or Path(".env")
    texts = {key: str(value) for key, value in updates.items()}
    vault_on = (
        texts["CHIMERA_KEY_VAULT"].strip().lower() in _ON_WORDS
        if "CHIMERA_KEY_VAULT" in texts
        else get_settings().key_vault
    )
    # First, because it is the step that can refuse: a locked keychain fails the save before any
    # line of `.env` has changed.
    in_vault, fallback = write_credentials(texts, path=path, vault_on=vault_on)
    for key, value in updates.items():
        if key not in SCREEN_STORABLE:
            _write_env_var(path, key, str(value))
        # Also update the live process env, so the running gateway / get_settings() sees the new value
        # THIS session without a restart — a key added in the onboarding wizard is usable immediately
        # (Settings reads from os.environ; .env is only re-read on a fresh process).
        os.environ[key] = str(value)
    get_settings.cache_clear()  # the lru_cache must not serve stale settings after a write
    result: dict[str, Any] = {"updated": sorted(updates)}
    # Only when there is something to say: a save that touched no credential answers exactly as it
    # did before the vault existed (the response model fills both with [] for the client).
    if in_vault:
        result["in_vault"] = in_vault
    if fallback:
        result["vault_fallback"] = fallback
    return result


def vault_move(to: str, *, env_path: Path | None = None) -> dict[str, list[str]]:
    """Move the keys between `.env` and the OS vault, in either direction. See `api/key_vault.py`.

    Into the vault only with the switch on: the switch is the owner's statement that keys belong
    there, and a move behind it would leave the screen saying "saves to .env" over a file of
    markers. Out of the vault always — it is the way back, and it has to work after the switch is
    off, which is exactly when someone wants it.
    """
    path = env_path or Path(".env")
    if to == "vault":
        if not get_settings().key_vault:
            raise ValueError("turn on CHIMERA_KEY_VAULT before moving keys into the vault")
        result = move_to_vault(path)
    elif to == "file":
        result = move_to_file(path)
    else:
        raise ValueError("to must be vault or file")
    get_settings.cache_clear()
    return result
