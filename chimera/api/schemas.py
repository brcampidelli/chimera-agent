"""Pydantic response models for the desktop API.

These exist so the OpenAPI schema (``/api/openapi.json``) describes the exact response shapes, which
lets the frontend GENERATE its TypeScript types from the backend (``npm run gen:api`` →
``src/lib/api-schema.ts``). That is the whole point: the contract lives in one place (here), and the
UI can't drift from it — a shape change regenerates the types and any mismatch becomes a TS error.

The route handlers still build plain dicts; attaching these as ``response_model`` validates and
serializes those dicts against the model (extra keys are dropped), so the models double as a runtime
contract check with no change to the handler bodies.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --- health / sessions ----------------------------------------------------------------------------


class HealthOut(BaseModel):
    status: str
    sessions: int


class VersionOut(BaseModel):
    version: str  # the running chimera version (from installed package metadata)
    latest: str | None  # the newest GitHub release tag (no leading "v"); null when the check couldn't run
    update_available: bool  # True ONLY when GitHub confirms a strictly-newer release (honest, fail-silent)
    notes_url: str | None  # the release page to read about the update; null when no update is available


class SessionMetaOut(BaseModel):
    id: str
    title: str
    turns: int
    updated_at: float


class AttachmentOut(BaseModel):
    """One stored attachment: an id to send with a turn, and what we managed to make of the file.

    Never the content. ``chars`` is how much text a document yielded, which is the honest way to say
    "we read it" — a document that converted to nothing is not the same as one we never opened, and
    ``note`` carries the reason when there is one.
    """

    id: str
    name: str
    kind: str
    chars: int = 0
    note: str = ""


class VisionOut(BaseModel):
    """Whether the model that would answer a turn can look at an image.

    ``support`` is ``"yes"``, ``"no"`` or ``"unknown"`` — three states because the source is a lookup
    table, and a table that has never heard of a model must not be allowed to report it as blind.
    """

    model: str
    support: str


class DictationOut(BaseModel):
    """Whether speech can become text here, and by which route.

    Reported before recording. The alternative — record, upload, fail — tells someone their audio
    could not be transcribed, which reads as "the recording was bad" rather than "nothing was ever
    going to transcribe it".
    """

    support: str  # "yes" | "no"
    how: str  # "local" | "openai" | ""


class TranscriptOut(BaseModel):
    """Dictated speech, as text. ``note`` is non-empty when transcription could not be done."""

    text: str
    note: str = ""


class TranscriberWarmOut(BaseModel):
    """Whether a local speech model was loaded ahead of the first utterance, and how long it took."""

    warmed: bool
    seconds: float


class FsMakeDirIn(BaseModel):
    """Where to create a folder, and what to call it.

    Two fields rather than one path, deliberately: a single string would put the whole guard on
    parsing, and the parent is not the caller's to choose freely — it is the folder the picker is
    already showing.
    """

    parent: str
    name: str


class FsMadeDirOut(BaseModel):
    """The folder, and whether this call is what made it.

    An existing folder is not an error — it is the answer, already true — but the screen should not
    say "created" about something that was already there.
    """

    path: str
    created: bool


class McpCatalogSecret(BaseModel):
    """One environment variable a catalogue entry needs, and where the user gets it."""

    key: str
    hint: str
    source: str = ""
    # The form the whole value must match (a regular expression), or "" for "anything not empty".
    # The screen refuses to save a value that fails it: a Stripe header pasted without "Bearer " is
    # refused by Stripe, and the bridge answers a refusal with a whole-user browser sign-in.
    pattern: str = ""


class McpCatalogEntry(BaseModel):
    """A verified way to run one MCP server.

    ``available`` is resolved on the SERVER, because only this process can see the machine's PATH —
    an entry offered where its runner is missing is the failure the catalogue exists to remove.

    ``containment`` is the field that carries its weight: for most servers what limits the damage is
    the CREDENTIAL (a database grant, a token scope), not the tool list, and a "read-only" badge
    would say the opposite.
    """

    id: str
    label: str
    summary: str
    runner: str
    available: bool
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    secrets: list[McpCatalogSecret] = Field(default_factory=list)
    containment: str = ""
    official: bool = True
    docs: str = ""
    # How the server authenticates: "oauth", "login", "key" or "url". Makes an entry with no
    # `secrets` a declaration ("signs in through the browser") rather than a possible omission.
    auth: str = "oauth"


class McpCatalogOut(BaseModel):
    entries: list[McpCatalogEntry]
    count: int


class DeletedCountOut(BaseModel):
    """How many records a delete actually removed.

    Distinct from :class:`DeletedOut`, which is a boolean and right for deleting ONE thing by id.
    A bulk delete has a third answer those two cannot tell apart: "the project was already empty"
    and "seven conversations are gone" are different sentences for the person who clicked.
    """

    deleted: int


class UiLayoutOut(BaseModel):
    """The desktop's stored screen layout, or null when none was stored (dynamic screen, phase 6).

    Opaque to the server on purpose: the client owns the layout model and reads anything it does not
    recognise as its default, so a shape checked here would be a second definition that drifts.
    """

    layout: dict[str, Any] | None


class UiLayoutIn(BaseModel):
    layout: dict[str, Any]


class CodeSessionMetaOut(BaseModel):
    """One row of the coding-conversation list.

    Separate from ``SessionMetaOut`` because it carries ``workspace`` — the field that lets the list
    be grouped by project instead of being a flat pile of past questions with no owner.
    """

    id: str
    title: str
    workspace: str
    turns: int
    updated_at: float
    #: A turn of this conversation is running now. Absent for a conversation nobody is working in.
    running: bool = False
    #: What the conversation needs, derived from facts the server holds and never by a model
    #: (`chimera/api/conversation_state.py`): a question waiting for you, a turn or background work
    #: running, a last turn that failed, edits you have not looked at, or nothing.
    state: Literal["running", "waiting", "failed", "review", "idle"] = "idle"
    #: When it was archived; None for a conversation in the list.
    archived_at: float | None = None


class CodeSessionArchiveOut(BaseModel):
    """A conversation after archiving or bringing it back: ``archived_at`` is None once it is back.

    Archiving touches no file, folder or worktree — it is a timestamp beside the transcripts. A
    refusal is a 409 with the reason (a turn running, a question waiting), an unknown id a 404.
    """

    id: str
    archived_at: float | None = None


class CodeSessionSeenOut(BaseModel):
    """Whether marking a conversation seen changed anything: false when there was nothing unseen."""

    changed: bool


class CodeProjectOut(BaseModel):
    """One registered project: where it is, and what its owner calls it.

    Distinct from the projects the sidebar derives from conversations. That grouping answers "where
    has work happened"; this answers "where do I work", which is a question a fresh install has to be
    able to answer before any work has happened in it.
    """

    path: str
    alias: str = ""
    shell_granted: bool = False
    """The owner let the agent run commands in this folder — the record the server enforces.

    A request that asks for the shell in a folder without this is answered with the reach below it
    (see ``assemble_registry``). It never beats a ``read_only`` reach nor ``CHIMERA_HOST_EXEC=deny``.
    """
    granted_at: str = ""
    """When it was granted, ISO-8601 UTC. Empty when not granted."""
    pinned: bool = False
    """Listed first, above the projects ordered by recency."""
    last_used_at: str = ""
    """When a coding turn last started here, ISO-8601 UTC. Empty = never."""
    hidden: bool = False
    """Removed from the lists by the owner. Kept as a row so a folder with conversations does not
    reappear the moment it is removed; hiding also revoked any grant and pin it had."""


class CodeProjectFlagsIn(BaseModel):
    """Pin or hide a project. An absent field says nothing about it, so pinning cannot unhide."""

    path: str
    pinned: bool | None = None
    hidden: bool | None = None


class CodeProjectGrantIn(BaseModel):
    """Grant or revoke commands in one folder. Its own route, so the bridge can hold it to Full."""

    path: str
    shell_granted: bool


class ProjectPackAcceptIn(BaseModel):
    """Accept one folder's pack — the file whose SHA-256 the screen showed, and only that file."""

    path: str
    digest: str


class ProjectPackOut(BaseModel):
    """One folder's ``.chimera/pack.json`` held against the owner's settings (study 29, P7.6).

    Everything a pack asks for is listed, including what it asked for and could not have: names it
    lists that are not switched on or not configured (clamped — never activated, never launched)
    and keys a pack cannot set (``ignored``). A pack only narrows, and the card is where that is
    seen rather than assumed.
    """

    #: ``CHIMERA_PROJECT_PACK``. Off, no pack applies anywhere, accepted or not.
    enabled: bool = False
    present: bool = False
    #: Why a present pack is refused whole; empty when it reads cleanly.
    error: str = ""
    digest: str = ""
    #: The owner accepted exactly these bytes for this folder.
    accepted: bool = False
    #: An earlier version was accepted and the file has changed since (or was broken, or deleted).
    changed: bool = False
    #: ``changed``, and the version the owner accepted keeps applying from their record until they
    #: accept the new file or revoke. A change to the file never lifts a narrowing.
    held: bool = False
    #: The switch is on and an accepted pack — this file, or the held version — narrows runs here.
    applied: bool = False
    skills: list[str] | None = None
    mcp: list[str] | None = None
    tools_deny: list[str] = Field(default_factory=list)
    ignored: list[str] = Field(default_factory=list)
    skills_kept: list[str] = Field(default_factory=list)
    skills_hidden: list[str] = Field(default_factory=list)
    skills_not_active: list[str] = Field(default_factory=list)
    mcp_kept: list[str] = Field(default_factory=list)
    mcp_hidden: list[str] = Field(default_factory=list)
    mcp_not_configured: list[str] = Field(default_factory=list)
    tools_denied: list[str] = Field(default_factory=list)


class CodeGrantMigrationIn(BaseModel):
    """The folders the desktop had granted in its own browser storage, sent once."""

    paths: list[str] = Field(default_factory=list, max_length=500)


class CodeGrantMigrationOut(BaseModel):
    """What the one-time migration did. ``migrated=False`` = it had already happened; nothing changed."""

    migrated: bool
    recorded: int
    projects: list[CodeProjectOut]


class CodeProjectIn(BaseModel):
    """A project as a client registers it.

    ``alias`` absent means *say nothing about the name*, and an empty string CLEARS it. The two read
    identically in JSON unless the distinction is kept on purpose, and without it re-registering a
    project you have already named would wipe the name — so the default is ``None``, never ``""``.
    """

    path: str
    alias: str | None = None


class FsDirOut(BaseModel):
    name: str
    path: str


class FsBrowseOut(BaseModel):
    """One level of the folder picker: where we are, where up is, and what is inside."""

    path: str
    parent: str
    entries: list[FsDirOut]
    capped: bool


class CodeToolOut(BaseModel):
    name: str
    arguments: dict[str, Any]
    ok: bool
    observation: str


class CodeExchangeOut(BaseModel):
    """One question and everything the agent did answering it.

    ``edits`` is always empty on a REPLAY: a diff was streamed live and never entered the message
    list, so a resumed turn can show the tool call that wrote a file but not the coloured patch. The
    field is here so a replayed exchange has the same shape as a live one, and the UI says which is
    which rather than letting the absence read as "it changed nothing".
    """

    you: str
    answer: str
    tools: list[CodeToolOut]
    edits: list[dict[str, str]]
    done: dict[str, Any] | None = None
    """What the turn cost and what stopped it, as it was reported live.

    Stored per turn since receipts existed and absent on every conversation older than that, which
    is why it is nullable rather than defaulted to an empty object: "no accounting was kept for
    this turn" and "this turn cost nothing" are different statements, and the screen already draws
    them differently."""

    verified: dict[str, Any] | None = None
    """The verification verdict on what this turn WROTE, without its revert token.

    The token is deliberately not persisted: the undo offer is single-use and lives in memory, so
    replaying one would put a button on a reopened conversation that cannot do what it says."""


class CodeTurnFramesOut(BaseModel):
    """One coding turn's transcript from ``since`` onward, in the order its single writer stamped.

    The mechanism existed for orchestration and not for this route, which is the one that costs the
    most: a fan-out is recorded frame by frame and replayable, while a coding turn that lost its
    connection was gone. Same shape on purpose — the client already owns a reducer that ignores a
    `seq` it has applied, and two shapes would mean two reducers and eventually two behaviours.
    """

    turn_id: str
    frames: list[dict[str, Any]]
    #: The highest `seq` in `frames`, or the `since` that was asked for when there are none. A
    #: client stores this and asks again from it, which is what makes a second replay cheap.
    seq: int


class CodeTurnStopOut(BaseModel):
    """A stop that reached a running coding turn. The turn ends at its next step, not at once."""

    turn_id: str
    stopping: bool


class WorkOut(BaseModel):
    """A background work of a conversation (``chimera.api.works``): a coding turn run on the
    strong model while the conversation goes on."""

    id: str
    parent: str
    session_id: str
    turn_id: str
    workspace: str
    title: str
    model: str = ""
    state: str
    """``queued`` | ``running`` | ``waiting`` (a card is up) | ``done`` | ``failed`` | ``stopped`` | ``undone``."""
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    edits: list[str] = Field(default_factory=list)
    tools: int = 0
    steps: int = 0
    usd: float | None = None
    answer: str = ""
    error: str = ""
    verified: str = ""
    can_undo: bool = False
    reported: bool = False
    author: str = ""
    number: int = 0


class WorksOut(BaseModel):
    works: list[WorkOut]


class WorkActionOut(BaseModel):
    ok: bool
    work: WorkOut
    reason: str = ""


class RunningTurnOut(BaseModel):
    """A coding turn that is running now, as much of it as a screen needs to follow it.

    A conversation is stored when the agent finishes, so while a turn works the file has nothing of
    it. This is the pointer to it: which turn, what it was asked, and where on the conversation's
    live stream its opening frame is, so a screen that comes back can replay the turn from the start.
    """

    turn_id: str
    session_id: str
    workspace: str
    message: str
    started_at: float
    #: Ask the conversation's live stream for frames after this and the turn comes back whole.
    live_since: int
    #: The stored conversation already holds this turn's exchange (the agent has finished and the
    #: turn is verifying), so a screen that follows it must not draw that exchange twice.
    transcript_saved: bool


class CodeSessionOut(BaseModel):
    id: str
    workspace: str
    exchanges: list[CodeExchangeOut]
    #: Set while a turn of this conversation is running. A screen follows it instead of showing a
    #: conversation that looks idle while it is working.
    running_turn: RunningTurnOut | None = None


class CodeSessionRawOut(BaseModel):
    """A stored conversation's file, unparsed.

    ``text`` is the bytes on disk, not a re-serialisation. Everything else the API says about a
    session has been through a parser and folded into exchanges, which is what you want on screen
    and useless when the question is what the parser dropped.
    """

    id: str
    text: str
    #: Size of ``text`` in UTF-8 bytes — the one number that answers "why is this conversation slow"
    #: without reading it.
    bytes: int


class TurnOut(BaseModel):
    user: str
    assistant: str


class SessionDetailOut(BaseModel):
    id: str
    turns: list[TurnOut]


class NewSessionOut(BaseModel):
    id: str


class DeletedOut(BaseModel):
    deleted: bool


# --- config / doctor ------------------------------------------------------------------------------


class TiersOut(BaseModel):
    weak: str
    mid: str
    top: str


class ModelsCfgOut(BaseModel):
    default: str
    weak: str
    mid: str
    orchestrator: str
    cost_mode: str
    cascade: bool
    api_base: str | None
    fallback_models: list[str]
    tiers: TiersOut
    ollama_base_url: str = ""
    #: LM Studio's OpenAI-compatible root (``/v1`` included); what LiteLLM's ``lm_studio/`` needs.
    lm_studio_base_url: str = ""
    complete_model: str = ""
    #: The model that answers spoken TALK; "" is the conversation's own (``Settings.voice_model``).
    voice_model: str = ""
    #: The model that does spoken WORK; "" is the conversation's own (``Settings.voice_work_model``).
    voice_work_model: str = ""
    """Where the local Ollama server lives.

    Distinct from ``api_base``, which applies to EVERY call: this one only points LiteLLM's Ollama
    provider somewhere, so a remote Ollama box stops being a reason to hand-edit ``.env``."""


class MemoryCfgOut(BaseModel):
    backend: str
    semantic: bool
    auto_consolidate: bool
    remember_from_chat: bool
    skill_cards: bool = False
    """Whether a learned skill is injected back into the prompt when it matches the task.

    Off by default, and that default is the gap between what this project says it does and what an
    install does: the agent extracts skills from successful runs either way, and without this it
    never reads one of them again."""

    embed_model: str = ""
    """The model that turns memories into vectors — what makes ``semantic`` mean anything.

    Exposed because the screen already offers the switch that depends on it. Semantic recall falls
    back to lexical on ANY embedder failure, silently and by design, so a default the user's key
    cannot serve produced a toggle that turned on, confirmed, and changed nothing — the exact failure
    the ``applies`` field exists to prevent elsewhere."""


class CacheCfgOut(BaseModel):
    completion: bool
    prompt: bool


class SandboxCfgOut(BaseModel):
    mode: str
    image: str
    network: Literal["none", "bridge"] = "none"
    """The docker sandbox's network as the factory reads it. Means something only when the sandbox
    that answers is a container — see ``SandboxStateOut.network`` for what a command can reach."""
    verify_network: bool = False
    """``CHIMERA_VERIFY_NETWORK``: the verifier's own exception to ``network``. On, a docker sandbox
    is rebuilt for the verify command with the network open, and under a kernel sandbox a verify
    command the user typed runs on the host (``chimera.core.verify``)."""


class FusionKinshipOut(BaseModel):
    """How independent the judge is from the panel it grades — reported, never enforced.

    Two degrees, and the second is the one a slug does not reveal: ``judge_is_panelist`` is the judge
    grading its own answer, and ``judge_shares_vendor_with`` is the judge and a panelist coming from
    the same lab — not the same model, and not two independent votes either.
    """

    judge_is_panelist: bool = False
    judge_shares_vendor_with: list[str] = Field(default_factory=list)
    independent: bool = True


class FusionCfgOut(BaseModel):
    """Who plays each part when a turn is fused: the panel answers, the judge grades, the
    synthesizer writes. Editable, because the shipped default was the only cast anyone could run —
    and a default that cannot be changed is not a choice."""

    panel: list[str] = Field(default_factory=list)
    judge: str = ""
    synthesizer: str = ""
    mode: str = "selective"
    kinship: FusionKinshipOut = Field(default_factory=FusionKinshipOut)


class AutonomyCfgOut(BaseModel):
    """How much the agent may do — the three controls that decide it, in one place.

    ``reach`` and ``approval`` are empty when the deployment states no posture, which is not the same
    as stating a permissive one: empty means a request's own posture is the only one in force, and
    that is the behaviour every existing client has. Set either and it becomes a floor the request
    cannot raise.
    """

    reach: str = ""
    approval: str = ""
    host_exec: str = "ask"
    denied_tools: list[str] = Field(default_factory=list)
    governance: str = "off"
    """Whether anything JUDGES what a run does — ``off`` | ``observe`` | ``enforce``.

    The fourth control, and the one that was missing from every screen. The three above decide
    what a run may reach; this decides whether the trust kernel is in the path at all. It ships
    ``off``, so on a stock install the only thing between the model and a `git push --force`
    inside the workspace is the folder jail."""

    egress_allow: list[str] = Field(default_factory=list)
    """Hosts where a query-string GET is not treated as a way out while the run holds untrusted
    content. Empty by default: nothing is exempt until the owner names it.

    Reported as the values, unlike the webhook above, because this is not a credential — it is a
    statement the owner made, and a list you cannot read back is one you cannot correct."""
    approval_webhook_set: bool = False
    """Whether this deployment has said where an approval question goes.

    A fact about configuration, never the value: the URL is a credential and whoever holds it
    can post into that channel. Without one, a review on an unattended surface is a refusal —
    which the refusal now says, naming this setting."""
    pull_requests: bool = False
    """``CHIMERA_PULL_REQUESTS``: whether the agent has ``open_pull_request``. Off by default, and a
    server without the field is off. On or off, every pull request the agent proposes asks the owner."""


class ServerCfgOut(BaseModel):
    token_set: bool


class McpCfgOut(BaseModel):
    autoload: bool  # settings.mcp_autoload — when on, configured MCP tools load at app start


class ProviderOut(BaseModel):
    """One credential slot, as the screen that offers it needs to understand it."""

    env: str
    #: The provider's routing name (``"openrouter"``) — the env var without its suffix, lowercased.
    #: Sent so a client asking a provider-scoped question does not have to re-implement that rule and
    #: drift from :func:`chimera.providers.discovery.provider_from_env_var`.
    name: str = ""
    label: str
    set: bool
    hint: str
    llm: bool = True
    """Whether this credential serves MODELS, as opposed to search, speech or images.

    The screen listing keys shows all of them; the first-run wizard must offer only these. Saving a
    web-search key does not make ``has_any_key`` true, so a wizard that let you pick one would take
    the key, confirm it, and then stay on screen forever waiting for a provider."""

    model: str = ""
    """A model slug that works the moment this key is saved; empty when we have no suggestion.

    Answers here rather than in the client because the client is the one place it cannot be checked:
    a list of slugs maintained in the frontend goes stale silently, and this one is already known to
    be perishable. See ``chimera.providers.catalog.ProviderInfo``."""

    keys_url: str = ""
    """Where to get a key. Empty for a provider we discovered rather than one we ship."""

    in_vault: bool = False
    """Whether this key is held by the OS vault rather than by ``.env`` (study 29, P7.7). A fact
    about where it lives — the value is never sent, from either place."""


class PoolKeyOut(BaseModel):
    """One key in a rotation pool, as much of it as anyone is ever shown."""

    index: int
    """Its position, which is how the client asks for it to be removed.

    An index rather than the value, and that is the whole design: there is no request shape that can
    carry a key back to the server, so a client that renders the mask and re-submits the field — the
    obvious bug, and the one that would overwrite a working pool with `…abcd` — cannot be written."""

    hint: str


class PoolOut(BaseModel):
    """A provider's rotation pool. Present for every provider that has one, empty list when it does not."""

    provider: str
    env: str
    keys: list[PoolKeyOut]


class AutomationCfgOut(BaseModel):
    """The scheduler daemon's master switch.

    The Schedule screen can create and enable jobs, but whether the daemon that fires them runs
    inside the desktop app is a separate setting — and it was readable only from the environment,
    so the UI could not show its own scheduler's state.
    """

    cron: bool
    notify_failures: bool = True
    """``CHIMERA_CRON_NOTIFY_FAILURES``: whether a job's channel hears that the job could not run.
    On by default, and a server without the field is on that default."""


class ConversationsCfgOut(BaseModel):
    """``CHIMERA_ARCHIVE_AFTER_DAYS``: archive a coding conversation left alone this many days.

    ``None`` is never, the shipped state. Archiving is a timestamp beside the transcripts; the rule
    never archives one with a turn running, a question waiting, unfinished background work or an
    open share link (``chimera/api/conversation_state.py``)."""

    archive_after_days: float | None = None


class ShellPrefsOut(BaseModel):
    """The desktop shell's own switches, as the shell reads them (``chimera/api/shell_prefs.py``).

    ``available`` is false on a server the desktop app did not start: there is no tray to change.
    ``start_at_sign_in`` is the operating system's answer as the shell last reported it, ``None``
    while unknown; ``sign_in_requested`` is a change asked for and not yet carried out."""

    available: bool
    keep_in_tray: bool = False
    call_attention: bool = True
    quick_entry: bool = False
    quick_entry_chord: str = ""
    start_at_sign_in: bool | None = None
    sign_in_requested: bool | None = None
    problem: str = ""
    """The tray's own problem line (a chord another program holds, a refused sign-in entry)."""
    unreadable: bool = False
    """The file does not parse, so the shell is on its defaults and a save is refused."""


class ShellPrefsIn(BaseModel):
    """The switches to change. An absent field is left as it is; the chord is not writable here."""

    model_config = ConfigDict(extra="forbid")

    keep_in_tray: bool | None = None
    call_attention: bool | None = None
    quick_entry: bool | None = None
    start_at_sign_in: bool | None = None


class WeeklyReviewOut(BaseModel):
    """The weekly-review job (``chimera/scheduler/weekly_review.py``) as the Settings row shows it.

    ``proposed`` is false until the job exists. ``posts_to`` is the destination's host only — the
    URL is a credential — and empty while the result goes to the results log alone."""

    proposed: bool
    job_id: str = ""
    enabled: bool = False
    posts_to: str = ""


class WeeklyReviewIn(BaseModel):
    enabled: bool


class MessagingCfgOut(BaseModel):
    """Who may talk to each chat bot, as the owner saved it (``chimera/server/allowlist.py``).

    ``allowed_users`` maps a platform (discord, telegram, slack, signal, whatsapp) to its ids. An
    empty list means ANYONE who can reach the bot — the behaviour every bot had before the setting
    existed — and the Messaging card warns about it instead of showing a blank field.
    """

    allowed_users: dict[str, list[str]] = Field(default_factory=dict)
    configured: list[str] = Field(default_factory=list)
    """The platforms whose bot has what it needs to start (``allowlist.bot_configured``). A server
    that predates the field omits it, and the card then treats every platform as connected."""
    discord_attach_files: bool = False
    """``CHIMERA_DISCORD_ATTACH_FILES`` as saved (study 29, P6.3). It attaches nothing while the
    Discord allowlist is empty, which the card says beside the switch."""


class GuardCfgOut(BaseModel):
    """Whether the CHAT agent is assembled with the coding turn's protections.

    Off by default, and that default is a real exposure rather than an oversight: this agent is
    shared with the messaging gateway and the OpenAI-compatible endpoint, so arming it silently would
    take shell away from bots someone already runs. Readable here because the posture line names this
    switch when it reports a conversation as unguarded — a warning that cannot point at its own
    remedy is half a warning.
    """

    chat: bool


class BrowserCfgOut(BaseModel):
    """Whether the agent's Chromium runs where you can see it.

    The switch has been wired since the browser tool shipped and had no control anywhere, so the only
    way to watch a page the agent was driving was to edit ``.env`` and restart — which is the same as
    not being able to watch it, for anyone who did not read the source.
    """

    headless: bool = True
    #: ``CHIMERA_BROWSER_SITES``, parsed: hosts and ``*.domain`` entries. Empty = any public site.
    sites: list[str] = Field(default_factory=list)
    #: ``CHIMERA_BROWSER_LOCAL_PORTS``, parsed. Empty = no loopback at all, as before study 29 P5.2.
    local_ports: list[int] = Field(default_factory=list)
    #: Why the two lists above cannot be read, when ``.env`` holds a value that does not parse (a
    #: hand edit; the screen refuses one). Then the browser is left out of EVERY conversation, which
    #: the empty lists alone would misreport as "any public site". None when both parse.
    invalid: str | None = None


class ExperimentalCfgOut(BaseModel):
    """Three study-25 modules that ship behind a switch, each OFF because its measurement did not
    recommend it (the reason sits beside each field in ``chimera/config.py``).

    Readable here so the Settings screen can offer them with the measured caveat on the row, instead
    of leaving them to people who read the source. Defaults mirror ``Settings``: a server that does
    not send this block is a server where all three are off.
    """

    browser_situation: bool = False
    research_agent: bool = False
    explorer_contract: bool = False


class DeferCfgOut(BaseModel):
    """The two deferral switches — tools reached on demand instead of declared on every step.

    A block of their own rather than three more fields in ``experimental``: that one is the
    study-25 set and is asserted as exactly those three. Both OFF by default (``chimera/config.py``
    says why beside each), and a server that predates this block reads as both off, which is what
    such a server does.
    """

    #: Built-in tools beyond files/search/shell reached through `tool_list` (``CHIMERA_DEFER_TOOLS``).
    tools: bool = False
    #: MCP servers reached through `mcp_list` instead of declared (``CHIMERA_MCP_DEFER``).
    mcp: bool = False


class ProjectPackCfgOut(BaseModel):
    """``CHIMERA_PROJECT_PACK`` — whether a project's ``.chimera/pack.json`` may narrow a run.

    Off by default, and a server that predates the block reads as off, which is what it does.
    """

    enabled: bool = False


class BridgeCfgOut(BaseModel):
    """The desktop bridge's two switches (``chimera/api/desktop_bridge.py``), both off by default.

    ``full`` is reported as the owner set it, not as it acts: it does nothing while ``enabled`` is
    off, and the screen says so rather than hiding the row.
    """

    enabled: bool = False
    full: bool = False


class DecisionsCfgOut(BaseModel):
    """Which backend answers a typed decision and which model (``chimera/config.py``). An empty model
    is the backend's measured default; a server without the block is on the shipped default."""

    backend: str = "local_logprob"
    model: str = ""
    verified_answers: bool = True
    """Whether an answer drafted from attached sources with no tool call is checked by this backend
    before it ships (``chimera/fusion/verified.py``, study 26)."""
    verified_answers_threshold: float = 0.8


class SpendCfgOut(BaseModel):
    """The day's dollar ceiling (``CHIMERA_DAILY_USD_CAP``). ``None`` is no cap, the shipped state.

    It brakes SCHEDULED jobs only (``chimera/scheduler/job_runner.py``): a chat or Code turn is not
    stopped by it, and the Usage screen says so beside the field rather than letting "daily cap"
    read as a ceiling on everything."""

    daily_usd_cap: float | None = None


class KeepAwakeCfgOut(BaseModel):
    """What the owner chose for ``chimera/core/keep_awake.py``. Off by default; a server without
    the block is on that default."""

    mode: str = "off"
    on_battery: bool = False


class StorageCfgOut(BaseModel):
    """``CHIMERA_WORKTREE_DIR`` as set; empty is the system temp folder (the default)."""

    worktree_dir: str = ""
    branch_prefix: str = "chimera"
    """``CHIMERA_BRANCH_PREFIX``: the first segment of an isolated run's branch, ``<prefix>/attempt-…``."""


class SharingCfgOut(BaseModel):
    """Whether conversations may be shared and how long a new link opens one (``CHIMERA_SHARING``,
    ``CHIMERA_SHARE_EXPIRY_HOURS``). A server without the block is on the shipped default: sharing
    on, links that never expire — what sharing did before either setting existed."""

    enabled: bool = True
    expiry_hours: float | None = None


class KeepAwakeOut(BaseModel):
    """Whether this process is holding the machine awake right now, and why.

    ``active`` is the OS state, not the setting: ``working`` with nothing running is not active, and
    work present on battery is ``blocked="battery"``. The status bar shows a line only while
    ``active`` is true, so it can never claim a hold that is not there."""

    mode: str = "off"
    active: bool = False
    reasons: list[str] = Field(default_factory=list)
    """``turn``, ``work``, ``run``, ``cron``, ``always`` — what is keeping it up."""
    blocked: str = ""
    """``battery`` or ``unsupported`` when there is work and the machine is NOT held; else empty."""
    mechanism: str = ""
    """``windows``, ``systemd-inhibit``, ``none``; empty until the keeper first needed one."""
    on_battery_allowed: bool = False


class PromptRouteOut(BaseModel):
    """One provider a configured model role would send a prompt to (``prompt_routes``)."""

    provider: str
    local: bool = False
    """A keyless runtime whose URL is a loopback address — the prompt stays on this machine."""
    host: str = ""
    """For a keyless-runtime prefix (``ollama_chat/``, ``lm_studio/``…) that is NOT local: the host it
    is sent to (Ollama Cloud, a remote server). Empty for a local or a hosted provider."""
    roles: list[str] = Field(default_factory=list)
    """``default``, ``weak``, ``fusion_judge``, ``embeddings``, ``decisions``… — why it is listed."""


class PrivacyCfgOut(BaseModel):
    """The Security screen's privacy card (``chimera/providers/privacy.py``). Read-only facts plus
    the two OpenRouter switches; a server without the block is on the shipped defaults, which send
    nothing."""

    openrouter_data_collection: str = "allow"
    """``allow`` (the default: nothing sent) or ``deny`` (only routes that keep no prompts)."""
    openrouter_zdr: bool = False
    agent_reads_own_env: bool = True
    """Whether the agent's read tools may read Chimera's own ``.env`` (``CHIMERA_AGENT_READS_OWN_ENV``,
    on by default). On, the provider keys saved there can reach the model; off, that one file is
    refused or hidden by every read tool."""
    routes: list[PromptRouteOut] = Field(default_factory=list)
    telemetry: bool = False
    """Whether anything is exported: OpenTelemetry asked for (``CHIMERA_OTEL`` or
    ``OTEL_EXPORTER_OTLP_ENDPOINT``) AND the ``[otel]`` extra installed."""
    telemetry_requested: bool = False
    """Whether it was asked for, installed or not — so "requested, nothing exported" can be said."""
    unscoped: list[str] = Field(default_factory=list)
    """Surfaces that reach OpenRouter WITHOUT the preference above, so the card can say so instead of
    letting ``deny`` read as covering every call: ``decisions`` (the Decisions API is the chosen
    backend) or ``decisions_fallback`` (it stands behind the local verifier — verified answers on,
    ``local_logprob``, an OpenRouter key)."""


class VaultCfgOut(BaseModel):
    """Where the Settings screen saves a key — ``chimera/api/key_vault.py``.

    ``enabled`` is the owner's switch (``CHIMERA_KEY_VAULT``, off by default). ``available`` is
    whether this machine has a vault at all: false without the ``secrets`` extra, on a headless box,
    or in a build that could not bundle one — and then a save with the switch on goes to ``.env``
    and says so. ``keys`` are NAMES, asked of the vault only when the switch is on or ``.env`` marks
    a key as moved, so an owner who never opted in gets no keychain access from this read."""

    enabled: bool = False
    available: bool = False
    keys: list[str] = Field(default_factory=list)


class ConfigOut(BaseModel):
    models: ModelsCfgOut
    fusion: FusionCfgOut = Field(default_factory=FusionCfgOut)
    memory: MemoryCfgOut
    cache: CacheCfgOut
    sandbox: SandboxCfgOut
    browser: BrowserCfgOut = Field(default_factory=BrowserCfgOut)
    experimental: ExperimentalCfgOut = Field(default_factory=ExperimentalCfgOut)
    defer: DeferCfgOut = Field(default_factory=DeferCfgOut)
    project_pack: ProjectPackCfgOut = Field(default_factory=ProjectPackCfgOut)
    bridge: BridgeCfgOut = Field(default_factory=BridgeCfgOut)
    decisions: DecisionsCfgOut = Field(default_factory=DecisionsCfgOut)
    spend: SpendCfgOut = Field(default_factory=SpendCfgOut)
    keep_awake: KeepAwakeCfgOut = Field(default_factory=KeepAwakeCfgOut)
    storage: StorageCfgOut = Field(default_factory=StorageCfgOut)
    sharing: SharingCfgOut = Field(default_factory=SharingCfgOut)
    autonomy: AutonomyCfgOut
    server: ServerCfgOut
    mcp: McpCfgOut
    automation: AutomationCfgOut
    conversations: ConversationsCfgOut = Field(default_factory=ConversationsCfgOut)
    messaging: MessagingCfgOut = Field(default_factory=MessagingCfgOut)
    privacy: PrivacyCfgOut = Field(default_factory=PrivacyCfgOut)
    guard: GuardCfgOut
    providers: list[ProviderOut]
    vault: VaultCfgOut = Field(default_factory=VaultCfgOut)
    pools: list[PoolOut] = Field(default_factory=list)
    """Multi-key rotation, per provider. Never the keys — see :class:`PoolKeyOut`."""

    applies: dict[str, str] = Field(default_factory=dict)
    """Env-var name -> when a saved change starts applying, for the ones where it is not "now".

    Only the exceptions are listed; a key absent here applies to the next call. The screen reads
    this instead of carrying its own list, so a control can never quietly outlive the reason it was
    labelled — see ``chimera.api.config_api.APPLIES_WHEN``."""

    pinned: list[str] = Field(default_factory=list)
    """Env-var names this deployment inherited from its environment rather than from ``.env``.

    A save for one of these writes the file, reports success, holds for the session — and is
    overridden again at the next launch, because a real environment variable outranks ``.env``. That
    is the one failure mode a settings screen cannot recover from on its own, since nothing about it
    is visible until days later, so the server names the keys and the screen says so on the row.

    Empty on an ordinary desktop install, which is the point: this is a container/systemd/remote
    concern, and it becomes reachable precisely because the app can be pointed at a server somebody
    else deployed (``apps/desktop/src/lib/server.ts``)."""


class AgentIdentityOut(BaseModel):
    """Who the agent is, in the words of the person who runs it.

    Returned on write as well as on read, and deliberately not an echo of what was sent: the free
    text is capped, so a caller that pasted more than the budget must see what the agent will
    actually be told rather than what they typed.
    """

    name: str = ""
    language: str = ""
    instructions: str = ""


class AgentDefOut(BaseModel):
    """One agent you can send work to — as distinct from the one you converse with.

    Shares its field names with ``AgentIdentityOut`` on purpose: an agent is an agent, and the two
    should be copyable between each other without a translation layer. What it adds is an ``id``
    (its handle, and the Kanban lane it answers on) and a tool allowlist.
    """

    id: str
    name: str = ""
    instructions: str = ""
    model: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    """Empty means NO RESTRICTION, not "no tools" — the same reading every other list in this
    project's configuration has. The conversion to ``Role``'s opposite convention happens in one
    place, server-side."""


class UpdatedOut(BaseModel):
    updated: list[str]
    in_vault: list[str] = Field(default_factory=list)
    """The credentials this save put in the OS vault; their ``.env`` lines are now markers."""

    vault_fallback: list[str] = Field(default_factory=list)
    """Credentials saved to ``.env`` although the vault switch is on, because this machine has no
    vault. Named so the screen can say it, rather than a switch reading "on" over a plain-text file."""


class VaultMoveIn(BaseModel):
    """Which way to move the keys: ``vault`` (out of ``.env``) or ``file`` (back into it)."""

    to: Literal["vault", "file"]


class VaultMoveOut(BaseModel):
    """What a move did, by NAME. ``failed`` stayed where it was; ``skipped`` had a value in ``.env``
    already, which is the one in force, so the vault copy was left alone rather than written over it."""

    moved: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    too_large: list[str] = Field(default_factory=list)
    """Left in ``.env`` without trying: larger than this machine's vault holds per entry (Windows'
    Credential Manager, 2560 bytes — a long rotation pool reaches it). Named apart from ``failed``
    because the reason is known, and "it refused" would send the owner after a locked vault."""


class PoolAddIn(BaseModel):
    key: str


class PoolWriteOut(BaseModel):
    """What a pool looks like afterwards — the count, never the contents."""

    provider: str
    count: int
    in_vault: list[str] = Field(default_factory=list)
    """The pool variable, when this write put it in the OS vault (study 29, P7.7)."""

    vault_fallback: list[str] = Field(default_factory=list)
    """The pool variable, when it went to ``.env`` although the vault switch is on, because this
    machine has no vault — named so the pool card can say it."""


class DiagnosticOut(BaseModel):
    """One problem the language server found, in the editor's own coordinates."""

    path: str
    line: int  # 0-based, as the protocol sends it
    #: UTF-16 code units, as the protocol sends them — NOT a Python or JavaScript string index.
    #: JavaScript strings are UTF-16, so a browser can use this directly; anything server-side has
    #: to convert (see `chimera.lsp.positions`), and the two disagree only in files with an emoji
    #: or CJK before the problem, which is what makes the mistake invisible until it is reported.
    column: int
    end_line: int
    end_column: int
    severity: str  # "error" | "warning" | "information" | "hint"
    code: str  # the rule that fired, e.g. "F401"
    message: str


class DiagnosticsOut(BaseModel):
    """What the language server says about one file, and whether it was able to say anything."""

    diagnostics: list[DiagnosticOut]
    #: False when no language server could be started here. The list is then empty for a reason
    #: that is not "the file is clean", and a screen showing no squiggles either way would be
    #: reporting a clean bill of health nobody checked.
    available: bool
    #: What to do about `available: false`, in the words of an install line. Empty when running.
    note: str


class CompletionOut(BaseModel):
    """One inline suggestion, or an account of why there is none."""

    text: str
    #: An id to refer back to when the suggestion is accepted or dismissed. Empty when nothing was
    #: shown, which is what keeps the acceptance rate's denominator honest.
    id: str
    #: False ONLY for a standing problem — no model server, model not pulled, a runtime error. A
    #: superseded request and an empty answer are `True`: unlike a diagnostic, a missing suggestion
    #: asserts nothing about the file, so a banner for the ordinary cases would bury the real ones.
    available: bool
    note: str
    #: Round trip in milliseconds, which is the number that decides whether this feature is usable.
    ms: int
    model: str


class AcceptanceOut(BaseModel):
    """How often the suggestions are taken, on this machine.

    `rate` is null until something has been accepted or dismissed. Zero would be a claim ("nobody
    wants these") where the truth is that nobody has answered yet.
    """

    shown: int
    accepted: int
    dismissed: int
    rate: float | None
    mean_ms: int | None
    note: str


class SuggestionEventIn(BaseModel):
    """One event of a next-step suggestion under a Code-screen answer (:mod:`chimera.api.suggestion_log`).

    The kind and the event, never the suggestion's text: an acceptance rate does not need to know
    which files a "commit" suggestion named."""

    event: Literal["shown", "picked", "sent"]
    kind: Literal["fix", "commit", "continue"]
    #: For ``sent``: the picked text was changed before it went. Ignored for the other two.
    edited: bool = False


class SuggestionCountsOut(BaseModel):
    """Shown, picked and sent, and the two rates between them — each null with no denominator."""

    shown: int
    picked: int
    sent: int
    #: Of ``sent``, how many were changed in the box first.
    edited: int
    #: picked / shown. Null until something was shown.
    pick_rate: float | None
    #: sent / picked. Null until something was picked.
    send_rate: float | None


class SuggestionStatsOut(SuggestionCountsOut):
    """How often the suggestions under an answer are taken, on this machine, in total and per kind."""

    by_kind: dict[str, SuggestionCountsOut]
    note: str


class LocalRuntimeOut(BaseModel):
    """One local, keyless model runtime and what it has right now — Ollama or LM Studio.

    Same three-state answer as ``OllamaModelsOut`` (reachable with nothing, unreachable, reachable
    with models) and for the same reason; ``prefix`` is what turns an entry of ``models`` into the
    slug a turn runs on, so the client never re-implements which runtime takes which LiteLLM route.
    """

    #: ``"ollama"`` or ``"lm_studio"`` — a token, translated on the client.
    name: str
    base_url: str
    reachable: bool
    #: Prepend to a model to get its slug: ``ollama_chat/`` or ``lm_studio/``.
    prefix: str
    models: list[str] = Field(default_factory=list)
    reason: str = ""


class LocalRuntimesOut(BaseModel):
    """Every local runtime this install knows how to ask, asked. The first-run screen reads it to
    offer a model that is already on the machine instead of demanding a key."""

    runtimes: list[LocalRuntimeOut] = Field(default_factory=list)


class JobOut(BaseModel):
    """One background job — a `run_shell(background=true)` the agent started and did not wait for.

    ``state`` is ``running`` | ``finished`` | ``cancelled`` | ``timed_out`` | ``lost``.
    ``timed_out`` means it reached ``max_runtime`` and was killed with everything it started.
    ``lost`` is honest about a process nobody is watching any more (the app that started it
    restarted): its exit code was never seen, so "finished" would be a number nobody observed.
    ``reported`` says whether a turn has already been told it ended.
    """

    id: str
    command: str
    cwd: str
    pid: int
    started_at: float
    log: str
    state: str
    exit_code: int | None = None
    finished_at: float | None = None
    reported: bool = False
    #: The process that started it (``<pid>-<random>``); "" on records older than owners.
    owner: str = ""
    #: The runtime cap it was started under, in seconds (0 = none).
    max_runtime: float = 0.0
    #: Facts about how it ended: ``ended_by`` (``app_exit``), ``pid_alive_when_lost``.
    extra: dict[str, Any] = Field(default_factory=dict)
    #: The last part of the log, for a screen that shows a job without opening its file.
    tail: str = ""


class JobLogOut(JobOut):
    """One job with a bounded slice of its log (``GET /api/jobs/{id}``)."""

    #: The first lines asked for; "" when none were.
    head: str = ""
    #: The log's size in bytes.
    log_size: int = 0
    #: True when lines between ``head`` and ``tail`` were not read.
    gap: bool = False


class JobsOut(BaseModel):
    jobs: list[JobOut] = Field(default_factory=list)


class OllamaModelsOut(BaseModel):
    """What the configured Ollama has pulled, so a model field stops being a memory test.

    ``reachable`` is a separate field from an empty ``models`` on purpose, and it is the whole point
    of this response. A picker rendered from an empty list says *you have no models*; that is a claim
    about the user's machine, and when nothing answered the door we have no basis for it. Reachable
    with nothing pulled and unreachable are two states with opposite remedies, so the client is given
    both and can say which one it is.
    """

    #: The URL that was asked, so the client names the same address the user typed rather than a
    #: default it assumed.
    base_url: str
    reachable: bool
    #: Tags as Ollama spells them (``llama3:latest``); prefix with ``ollama_chat/`` for a slug.
    models: list[str] = Field(default_factory=list)
    #: Why no list came back, as a token the client translates — never a sentence. Same shape and
    #: same reason as ``PostureFacts``: this app ships ten languages, and English prose from the
    #: server would leave the line that explains an unavailable feature untranslated.
    reason: Literal["", "no_url", "unreachable", "http_error", "not_ollama"] = ""


class ModelOptionOut(BaseModel):
    """One model the user could pick, with the facts that decide whether they should.

    Every optional field is optional because it is genuinely unknown for some source. ``tools: null``
    is the one that must not be read as ``false``: a coding turn without tool calling can only
    DESCRIBE an edit, so "we were not told" and "it cannot" deserve different words on screen.
    """

    #: Already prefixed for LiteLLM (``openrouter/…``, ``ollama_chat/…``) — the UI never assembles
    #: a slug.
    slug: str
    label: str
    vendor: str
    #: Which catalogue this came from: Chimera's curated list, OpenRouter's index, or local Ollama.
    source: Literal["catalog", "openrouter", "ollama"]
    context_k: int | None = None
    #: USD per 1M tokens. null = unknown (never guessed); 0.0 = genuinely free.
    input_per_m: float | None = None
    output_per_m: float | None = None
    tools: bool | None = None
    vision: bool | None = None
    free: bool = False
    #: In Chimera's curated catalogue — a model this project has actually run.
    recommended: bool = False


class ModelsOut(BaseModel):
    """The models this install can pick from, and what we failed to reach while listing them.

    ``reason`` is a token the client translates, never a sentence — same shape and same reason as
    ``OllamaModelsOut``. It is set NEXT TO a non-empty list whenever the remote index failed but the
    curated catalogue answered, so the picker can say *the full list is unavailable* instead of
    rendering an empty menu, which reads as *your key buys nothing*.
    """

    #: The model a turn runs on when nothing is chosen, so the picker can mark it rather than guess.
    default: str
    models: list[ModelOptionOut] = Field(default_factory=list)
    sources: list[Literal["catalog", "openrouter", "ollama"]] = Field(default_factory=list)
    reason: Literal["", "no_provider", "unreachable", "http_error", "unreadable"] = ""


class ExecCancelOut(BaseModel):
    """Whether a command was actually stopped."""

    #: False when there was nothing to stop: the command already finished, or it is running inside a
    #: non-local sandbox where no host process exists to signal.
    cancelled: bool


class GpuOut(BaseModel):
    """One GPU, as its own driver reports it. Every number is nullable on purpose."""

    name: str
    vram_total_mb: int | None
    vram_used_mb: int | None
    utilisation: float | None


class MemoryOut(BaseModel):
    total_mb: int | None
    used_mb: int | None
    percent: float | None


class ResourcesOut(BaseModel):
    """What this machine is spending, with every gap left as a gap.

    Nullable throughout, and that is the contract: a measurement that could not be taken is absent,
    never zero. Zero VRAM reads as "the GPU is idle" and zero CPU reads as "nothing is running" —
    both are claims about a machine, and on a laptop whose GPU we cannot see, neither is ours to
    make. `notes` says which tool was missing, in the words of what to install.
    """

    cpu_percent: float | None
    cpu_count: int | None
    memory: MemoryOut
    process_mb: int | None  # this process, not the machine: two different sentences
    gpus: list[GpuOut]
    notes: list[str]


class StorageCategoryOut(BaseModel):
    """One kind of thing this install keeps on disk. ``bytes`` null = not measured, never zero."""

    key: str
    bytes: int | None
    files: int | None
    paths: list[str]  # the existing places counted, so the owner can go and look
    note: str  # why `bytes` is null, in a few words; empty when it is a measurement


class StorageWorktreeOut(BaseModel):
    """One isolated worktree folder, and whether the prune would collect it.

    ``state`` is ``live`` (a run is using it, or it is too new to judge), ``orphan`` (the prune
    removes it) or ``kept`` (its maker cannot be identified, so it is not called dead on a guess).
    ``reason`` is a fixed word a screen translates.
    """

    path: str
    bytes: int | None
    state: str
    reason: str


class DiskOut(BaseModel):
    path: str
    total: int | None
    free: int | None


class StorageOut(BaseModel):
    """What this install keeps on disk, by kind (study 29, P5.3). Nullable like `ResourcesOut`."""

    home: str
    worktree_dir: str  # where the NEXT worktree goes: the configured folder, or temp
    categories: list[StorageCategoryOut]
    worktrees: list[StorageWorktreeOut]
    disks: list[DiskOut]
    rotatable_logs: list[str]  # the only logs the rotate action moves — named, not implied


class StorageConfirmIn(BaseModel):
    """The two storage actions remove files; each must be asked for in so many words."""

    confirm: bool = False


class WorktreePruneOut(BaseModel):
    removed: int
    bytes_freed: int
    kept: int  # maker unknown — left alone
    live: int  # a run is using it — left alone
    failed: int  # could not be deleted (a file held open)


class LogRotateOut(BaseModel):
    rotated: int
    bytes_freed: int  # only the dropped previous generations; a first rotation frees nothing
    failed: int


class CrashReportOut(BaseModel):
    """The desktop's last ``backend-crash.txt``, with credentials scrubbed before it left disk."""

    path: str
    modified: str
    text: str


class AppDiagnosticsOut(BaseModel):
    """What a bug report needs, in one place (study 29, P5.3)."""

    backend_version: str
    python: str
    platform: str
    home: str
    workspace: str
    worktree_dir: str
    crash: CrashReportOut | None  # null when there is none, or outside the desktop's layout
    report: str  # the plain-text summary the Copy button puts on the clipboard, scrubbed


class ExternalAgentOut(BaseModel):
    """One ACP agent Chimera knows how to launch, and whether it is here."""

    key: str
    label: str
    available: bool  # resolved on THIS machine, right now — never inferred from configuration
    command: str
    install_hint: str  # what to DO about `available: false`; "not found" helps nobody
    writes_directly: bool  # it has file tools of its own, so our write region is an offer it may decline
    notes: str


class EditorCapabilityOut(BaseModel):
    """One editor capability, measured on this machine."""

    key: str
    label: str
    available: bool
    #: Whether `available` was MEASURED or merely read from the configuration. Diagnostics resolve a
    #: program, so True; completion only knows that a model and a URL were set, so False — and the
    #: caller must not render the two with the same word.
    probed: bool = True
    detail: str
    #: The command that turns `available: false` into true. "Unavailable" without one is a shrug.
    hint: str


class CodePythonOut(BaseModel):
    """The interpreter ``execute_code`` uses when a snippet runs on this machine.

    The frozen desktop build has no interpreter of its own, so it is whatever PATH holds, or none;
    without this, a snippet that could not start reads in a transcript like a model that wrote bad
    code. Only the host half: whether a container answers instead is ``/api/governance/sandbox``.
    """

    path: str = ""
    source: Literal["interpreter", "path", "missing"] = "missing"
    frozen: bool = False
    looked_for: list[str] = Field(default_factory=list)


class DoctorOut(BaseModel):
    has_any_key: bool
    #: The default model runs on this machine and needs no key (``ollama_chat/…``, ``lm_studio/…``).
    #: Beside ``has_any_key`` rather than folded into it, because the two have different remedies:
    #: a screen that gates on keys alone showed a keyless Ollama user a wizard demanding one.
    local_model: bool = False
    #: ``has_any_key or local_model`` — the question the first-run gate actually asks.
    can_answer: bool = False
    configured_providers: list[str]
    default_model: str
    tiers: TiersOut
    memory_backend: str
    cache: bool
    sandbox: str
    external_agents: list[ExternalAgentOut] = []
    editor: list[EditorCapabilityOut] = []
    #: Whether the default model can be priced. A spend cap stops on a call it cannot price,
    #: so an unpriced default is a cap that refuses to work — said here, before one is set.
    spend: EditorCapabilityOut | None = None
    #: Which Python ``execute_code`` runs on THIS machine — see ``CodePythonOut``.
    code_python: CodePythonOut | None = None


class ConfigTestOut(BaseModel):
    ok: bool  # True ONLY after a real 1-token call authenticated — the sole honest "key works" signal
    model: str  # the model the test call used (the given one, or the default)
    error: str | None  # a short, secret-free failure message when ok is False; null on success


# --- memory ---------------------------------------------------------------------------------------


class MemoryItemOut(BaseModel):
    id: str
    content: str
    kind: str
    provenance: str
    source: str
    #: The folder this fact belongs to, or None for one that belongs everywhere. On screen because
    #: the default is now to save into the project you are in: unshown, a fact meant for every
    #: project gets quietly filed under one, stops arriving elsewhere, and nothing says why.
    project: str | None = None


class MemoryProfileOut(BaseModel):
    profile: str
    persona: list[MemoryItemOut]


class MemoryAddOut(BaseModel):
    status: str
    item: MemoryItemOut


class MemoryExportOut(BaseModel):
    """The whole memory as one file's text. Built here, saved by the client: nothing is uploaded.

    Secrets are masked again on the way out and ``metadata`` is not included — see
    :mod:`chimera.memory.export` for why each."""

    format: str
    filename: str
    media_type: str
    count: int
    content: str


class ClaudeImportCandidateOut(BaseModel):
    content: str
    """The fact as it would be stored: markup removed, secrets already masked."""
    file: str
    """Which file under the folder it came from, relative to that folder."""
    known: bool = False
    """Memory already holds this fact (same words, ignoring case and spacing): writing it is a no-op."""
    project: str | None = None
    """The folder it will be filed under, or null for a fact that will apply in every folder."""
    claude_project: str = ""
    """The Claude project slug the note sits under (``projects/<slug>/memory``), "" for a global note.
    Set with ``project`` null means a note about one repository that no registered folder matches —
    it would apply everywhere."""


class ClaudeImportPreviewOut(BaseModel):
    """What an import WOULD write. Producing this writes nothing."""

    path: str
    files: list[str]
    candidates: list[ClaudeImportCandidateOut]
    notes: list[str]


class ClaudeImportApplyOut(BaseModel):
    written: int
    """Candidates the selection named, handed to the merge (ADD + UPDATE + NOOP)."""
    ignored: int
    """Strings in the selection that are not a candidate of this folder — never written."""
    counts: dict[str, int]


class ConsolidateGroupOut(BaseModel):
    kind: str
    project: str | None = None
    """The folder every member belongs to (a cluster never spans two), which the merged fact keeps."""
    unverified: bool = False
    """A member is unverified, so the merged fact will be too — including the members that were not."""
    items: list[MemoryItemOut]


class ConsolidatePreviewOut(BaseModel):
    """The clusters a consolidation would merge. Computed without a model call or a write."""

    groups: list[ConsolidateGroupOut]
    can_answer: bool
    """Whether a model is configured to write the merged facts. The preview is free either way."""


class ConsolidateApplyOut(BaseModel):
    merged: int
    """Reviewed clusters merged into one fact."""
    skipped: int
    """Reviewed clusters the model answered with nothing: left as they were, though the call was made."""
    stale: int
    """Reviewed clusters that are no longer the same set of facts, so were left alone."""
    removed: int
    """Net facts removed (each merged cluster of N leaves one)."""
    usd: float | None
    """What the merges cost, or null when a model had no price — not zero."""


# --- skills ---------------------------------------------------------------------------------------


class SkillStatOut(BaseModel):
    name: str
    kind: str
    status: str
    provenance: str
    uses: int
    successes: int
    rate: float | None


class SkillsOut(BaseModel):
    stats: list[SkillStatOut]
    retirement_candidates: list[str]
    cards_read: bool = False
    """Whether a run actually retrieves these cards — ``CHIMERA_SKILL_CARDS``, off by default.

    Without it the screen lists cards marked *active* with ``0 uses`` and no explanation, and the
    only available reading is that the agent tried them and they were useless. The truth is that
    nothing consulted them: reading was measured (+16.7pp, a confidence interval including zero,
    +300% tokens) and left off. A count of zero means two very different things depending on this
    flag, and the screen was showing the count without the flag."""


class ApprovedOut(BaseModel):
    approved: bool


class RetiredOut(BaseModel):
    retired: bool


class LibraryCardOut(BaseModel):
    """One curated skill card, at the level the browser asked for.

    ``body`` is empty in the list and filled on the detail route — the progressive disclosure the
    card format is built around (metadata decides relevance, instructions load when chosen). Sending
    all twenty-three bodies to draw a list would be a quarter of a megabyte to render one line each.
    """

    name: str
    description: str
    version: str
    kind: str
    #: Where in the work it applies (define/build/verify/review/ship) — "" on a card that declares
    #: none, which an imported third-party card legitimately does.
    stage: str
    topic: str
    triggers: list[str]
    license: str | None
    body: str = ""
    #: True when a skill of this name is already in the user's store, so the app can say "imported"
    #: instead of offering an action that silently overwrites.
    imported: bool = False


class LibraryImportOut(BaseModel):
    imported: bool
    name: str
    #: `pending` when the card came in tainted and is held for review, `active` otherwise. The app
    #: needs the distinction: an imported card that is not retrievable yet looks identical to one
    #: that is, and the difference is the whole point of the review gate.
    status: str


# --- cron -----------------------------------------------------------------------------------------


class CronJobOut(BaseModel):
    id: str
    name: str
    trigger: str
    schedule: str
    action: str
    enabled: bool
    next_run: float | None
    schedule_description: str
    next_firings: list[float]
    last_run: float | None
    """When a dispatch was last ATTEMPTED — not whether it worked. See the three fields below."""
    last_status: str | None = None
    """`ok`, `error` or `timeout`. None on a job that has never been dispatched."""
    last_error: str | None = None
    consecutive_failures: int = 0
    """Since the last success. One failure is weather; forty is a broken job."""
    disabled_by: str = ""
    """Who switched a disabled job off: `human`, `brake` (the engine, after repeated failures), or
    `""` while the job is enabled or for a job stored before the field existed."""
    created_by: str
    workspace: str | None = None
    """The folder this job works in. None means the root the process was started with — which on a
    packaged build is the install directory, so the screen says so rather than leaving it blank."""
    deliver_to: str | None = None
    """A chat webhook URL the answer is posted to, or None to only write it to the result file."""
    verify: str = ""
    """The gate, echoed back so the screen can show whether this job has one. A job with an empty
    `verify` runs ungoverned by design, and that is worth reading on the row rather than inferring
    from its absence."""
    max_attempts: int = 1
    notify: Literal["always", "on_change", "failures_only"] = "always"
    """When the answer is posted to `deliver_to`: every answer (`always`, the default), only when
    it differs from the last one delivered (`on_change`), or only when the run failed
    (`failures_only`). The result file records every answer whatever this says."""
    tools: list[str] | None = None
    """The only tools this job may use. None = every tool; an empty list = none."""


class CronResultOut(BaseModel):
    """One dispatch that produced an answer, for the Schedule screen."""

    at: float
    job_id: str
    name: str
    action: str
    answer: str
    delivered: bool | None = None
    """None when the job named no webhook. Not the same as a delivery that failed, and the screen
    has to be able to tell those apart — one is "nobody asked for delivery", the other is "we tried
    and could not"."""
    delivery_detail: str = ""
    skipped: str = ""
    """Why this answer was NOT posted to the job's destination, when it was not: the job said it had
    nothing new, or its `notify` setting held it back. Empty when it was posted (or there was nowhere
    to post it). A third state beside `delivered`, because "held back on purpose" is neither "nobody
    asked" nor "we tried and failed"."""


class MessagingPlatformOut(BaseModel):
    """Per-platform messaging status: is a token set, is the adapter running, did it die."""

    configured: bool
    running: bool
    error: str | None = None


#: Why no kernel boundary applies. The OS causes come from `chimera.sandbox.os_sandbox`;
#: ``no_container`` is this layer's own — a Docker install whose container never answered has a
#: different cause from a machine with no OS sandbox, and borrowing the wrong sentence for it would
#: send someone to install bubblewrap over a Docker daemon that is simply not running.
SandboxReason = Literal[
    "", "windows", "bwrap_missing", "userns_refused", "seatbelt_missing", "unsupported_os",
    "no_container",
]


class SandboxStateOut(BaseModel):
    """Whether a command the model chooses can reach this machine, and if it can, why.

    On the Security screen because that is the one screen a person opens to ask what protects them,
    and until now it answered about prompt injection and the audit log and said nothing about the
    boundary around execution. The posture line above the composer does say it — but only after
    choosing a project and turning commands on, which is exactly the moment it is too late to be
    reading about it for the first time.
    """

    configured: str
    """What the install asked for: ``auto`` (the default), ``docker``, ``local``, ``none``."""
    backend: str
    """What would actually run a command here: ``seatbelt``, ``bubblewrap``, ``docker``, ``host``."""
    isolated: bool
    """True only when a kernel boundary really applies. Never inferred from `configured` — asking
    for a sandbox and getting one are different facts, and conflating them is how "I thought it was
    sandboxed" happens."""
    reason: str = ""
    """Why not, in a sentence a person can act on. Empty when it IS isolated.

    ENGLISH, always, and the fallback rather than the display value — see ``reason_code``."""
    reason_code: SandboxReason = ""
    """Which cause, for a screen to say in its reader's language.

    The app ships in ten languages and this panel was printing the server's English sentence beside
    its own translated prose, on the one screen a person opens to learn what protects them. Same
    shape and same reason as ``PostureFacts.fell_back_reason``. A client that meets a code it does
    not know falls back to ``reason``: a new cause must degrade to English, never to silence."""
    platform: str = ""
    """The OS, because the answer is different on each and the reason names it."""
    network: Literal["none", "bridge", "host"] = "host"
    """What a command can reach on the network here. ``none``: a container without one, or a kernel
    sandbox (which has none to give). ``bridge``: a container given the bridge — every destination,
    not an allowlist. ``host``: no fence, the machine's own network. Defaults to ``host`` because a
    reader that cannot tell must not be told the network is closed."""


class CronCreateIn(BaseModel):
    """Create a scheduled job from the UI (the CLI's `chimera cron add`, over HTTP)."""

    name: str
    schedule: str  # a 5-field cron expression, e.g. "0 7 * * *" for 07:00 daily
    action: str  # the task/prompt the agent runs when it fires
    workspace: str | None = None
    """The folder the job works in. Sent by the screen from the project the user chose; a client
    that omits it gets the process root, which is the previous behaviour."""
    deliver_to: str | None = None
    """Optional chat webhook (Discord or Slack) the answer is posted to when the job fires."""
    verify: str = ""
    """Shell command that decides whether a dispatch KEPT its work; empty means no gate.

    `CronJob` has carried this since the harness landed and no caller could write it — not this
    route, not `chimera cron add` — so for every user `verify` was always empty, the gate could
    never arm, and the scheduled run kept the accounting half of that change and none of the rest.
    A field nothing can set is a field nobody has.
    """
    max_attempts: int = 1
    """How many times one dispatch may try. Worth raising only alongside `verify`: without a gate
    nothing can tell a failed attempt from a finished one."""
    notify: Literal["always", "on_change", "failures_only"] = "always"
    """When the answer is posted to `deliver_to` (see `CronJobOut.notify`). A client that omits it
    gets `always`, the previous behaviour."""
    tools: list[str] | None = None
    """The only tools this job may use. Omitted = every tool, the previous behaviour."""


# --- tasks (kanban + projects) --------------------------------------------------------------------


class TaskCardOut(BaseModel):
    id: str
    title: str
    action: str
    column: str
    success: bool | None
    risk: str | None
    depends_on: list[str]
    lane: str = "solve"
    """Who works this card: a built-in lane, or the id of one of your agents.

    On the wire because a board where every card looks identical cannot show what it is actually
    about — which of your agents is going to pick this up is the question a lane answers."""

    verify: str | None = None
    """The command that judges this card, or null. Shown rather than only stored: a card with an
    executable gate and a card judged by a model reading prose are different promises."""

    result: str = ""
    """What the lane answered, once it has run. Empty until then."""


class KanbanCardIn(BaseModel):
    """A card as a client files it."""

    title: str
    action: str = ""
    """What to actually do. Empty falls back to the title, matching the CLI: a one-line card should
    not have to say the same sentence twice."""

    lane: str = "solve"
    verify: str | None = None


class KanbanMoveIn(BaseModel):
    column: str


class CronLateOut(BaseModel):
    id: str
    name: str
    schedule: str
    due_at: float
    """When it was supposed to run, as an epoch second — rendered in the reader's own zone."""

    behind_seconds: float


class CronFailingOut(BaseModel):
    id: str
    name: str
    consecutive_failures: int
    last_status: str
    last_error: str | None = None


class CronDaemonWatchOut(BaseModel):
    """What the daemon's heartbeat says, as of the moment the question was asked.

    Three-valued on purpose: ``alive`` and ``stale`` are judged against ``max_gap_seconds``
    (three ticks of the beat's own interval); ``unknown`` means a beat exists but carried no
    tick interval, so freshness cannot be judged without inventing a number — and the reader
    refuses to invent one. ``none`` is "no signal", not "dead": a daemon that has never run
    left no evidence either way.
    """

    verdict: Literal["alive", "stale", "unknown", "none"]
    age_seconds: float | None = None
    max_gap_seconds: float | None = None


class CronSilenceOut(BaseModel):
    """What the schedule is not telling you: what never ran, and what ran and lost.

    Two lists rather than one, because the responses have nothing in common. ``overdue`` means
    nothing dispatched — that is about the daemon, and on the desktop the daemon is the app, so the
    usual cause is that the app was closed when the job was due. ``failing`` means the job ran, on
    time, and lost every time; that is about the job. A single "problems" list would merge the one
    you fix by opening the app with the one you fix by rewriting the action.

    ``daemon`` is the third answer, from the heartbeat the daemon writes every tick: it can say
    "the daemon is dead" while both lists are still empty, which is the window a daily job leaves
    open for ~23 hours after a crash.
    """

    overdue: list[CronLateOut]
    failing: list[CronFailingOut]
    daemon: CronDaemonWatchOut
    grace_seconds: float
    """How far past its time a job may be before it counts as missed.

    Reported rather than assumed: "due four seconds ago" is a tick in progress, not a miss, and a
    reader who cannot see the threshold cannot tell a real gap from the clock."""


class AgentDesignIn(BaseModel):
    """Describe an agent in a sentence. One model call; nothing is saved."""

    description: str


class AgentDesignOut(BaseModel):
    """A proposed agent, in the shape the registry form already edits.

    Deliberately the same field names as ``AgentDefOut`` so the screen can review a design in the
    form it already has, rather than growing a second surface that has to be kept in step with the
    first.
    """

    id: str = ""
    name: str = ""
    instructions: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    note: str = ""
    """"" on success; a short reason in place of a 500 when the design did not come back."""


class SpecRequirementOut(BaseModel):
    """One obligation in a drafted spec, in the two forms it has to exist in at once.

    ``text`` is what the person approving reads; ``check``/``target`` is what actually runs. They
    are shown together on purpose — a drafted spec whose sentence does not describe its check is
    the failure this whole flow has to avoid, and the only way anyone can catch it is by seeing
    both.
    """

    id: str
    text: str = ""
    check: str
    target: str
    required: bool = True


class SpecDraftIn(BaseModel):
    """Describe what you want; get a spec back. One model call, nothing written."""

    description: str
    workspace: str | None = None


class SpecDraftOut(BaseModel):
    name: str
    requirements: list[SpecRequirementOut]
    refused_commands: int = 0
    """How many ``command`` requirements the draft asked for and did not get.

    A ``command`` check is a shell command run on this machine, and the drafter refuses to write
    one — see ``chimera/orchestration/draft.py``. Reported rather than dropped quietly: the spec
    now verifies less than the model intended, and its owner should know by how much."""

    refused_ids: list[str] = Field(default_factory=list)
    note: str = ""
    """Empty when the draft worked. When it did not, the reason, in place of a 500."""


class SpecWriteIn(BaseModel):
    """Write a reviewed spec into the project folder. No model call.

    Separate from drafting so the requirements that land on disk are the ones the person kept,
    not the ones the model proposed — the edit in between is the entire point of showing them.
    """

    name: str
    requirements: list[SpecRequirementOut]
    workspace: str | None = None


class SpecWriteOut(BaseModel):
    path: str
    """Where it landed, so the screen can start a project against it — and so its owner can find,
    read and commit the file that decides when their project is done."""


class ProjectStartIn(BaseModel):
    """Create a project from a spec.

    ``spec`` is a PATH, not spec text. The spec is the acceptance authority — the only thing that
    decides whether the project is done — so it belongs in the repository, versioned and reviewable,
    not in a text box whose contents nobody else can see. Writing one is a job for the coding
    conversation; starting a project against it is this.
    """

    spec: str
    workspace: str | None = None
    max_iterations: int = 20
    auto_approve: bool = False
    """Skip the plan-approval pause. Off by default, because the pause is the point: a project that
    starts working before anyone read its plan is a project nobody chose to run."""


class KanbanRunIn(BaseModel):
    """Dispatch the backlog. Every field optional, because the common case is "work the board"."""

    limit: int | None = None
    """How many cards to take. None works the whole backlog."""

    workspace: str | None = None
    """Where the cards are worked. None uses the app's workspace."""

    model: str | None = None
    """A model for the lanes that have none pinned. An agent's own pin still wins over it."""

    workers: int = 1
    """How many cards to work at once. 1 is the sequential board this endpoint has always been.

    Above 1 each card gets its own git worktree and the edits are merged back afterwards, because
    two of the lanes run the autonomous verify-or-revert loop against the workspace and would
    otherwise undo each other's files. A file two cards both changed arrives as a `conflict` frame
    rather than being silently resolved to one of them."""


class ProjectStateOut(BaseModel):
    id: str
    status: str
    iterations: int
    plan_approved: bool
    pending_card_id: str | None
    note: str
    max_iterations: int


class ProjectDetailOut(BaseModel):
    state: ProjectStateOut
    columns: dict[str, list[TaskCardOut]]


# --- usage (cost / usage dashboard) ---------------------------------------------------------------


class UsageTotalsOut(BaseModel):
    turns: int
    prompt_tokens: int
    completion_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    usd: float  # SUM of only the priced turns' cost — unknown prices are excluded, not summed as 0
    unpriced_turns: int  # count of turns whose price was unknown (usd is None)


class UsageDayOut(BaseModel):
    day: str  # "YYYY-MM-DD"
    turns: int
    prompt_tokens: int
    completion_tokens: int
    usd: float  # summed over ONLY the priced turns of this day
    unpriced: int  # turns this day whose price is unknown (usd None) — not folded into usd


class UsageModelOut(BaseModel):
    model: str
    turns: int
    prompt_tokens: int
    completion_tokens: int
    usd: float  # summed over ONLY the priced turns of this model
    unpriced: int  # turns of this model whose price is unknown (usd None)


class UsageSessionOut(BaseModel):
    session_id: str
    turns: int
    prompt_tokens: int
    completion_tokens: int
    usd: float  # summed over ONLY the priced turns of this session
    unpriced: int  # turns of this session whose price is unknown (usd None)


class UsageSummaryOut(BaseModel):
    totals: UsageTotalsOut
    by_day: list[UsageDayOut]
    by_model: list[UsageModelOut]
    by_session: list[UsageSessionOut]
    cache_hit_pct: float | None  # cache_read / (prompt + cache_read), or None when the denominator is 0
    route_mix: dict[str, int]  # {"single", "fusion", "cascade"} turn counts


# --- plan (planner preview — zero-edit dry run of the planner only) --------------------------------


class PlanOut(BaseModel):
    steps: list[str]  # the planner's concrete numbered steps (empty when the model produced none)
    text: str  # the same steps rendered as numbered lines — the seed for the editable preview
    note: str  # "" on success; a short, secret-free message when the planner call degraded (no 500)


class RequirementOut(BaseModel):
    """One atomic requirement pulled out of the task.

    ``kind`` is ``do`` (must happen), ``avoid`` (must not happen) or ``include`` (the result must
    contain it). The three are kept apart because a weak model drops ``avoid`` and ``include``
    first — "must do X" survives context growth and "don't do Y" quietly does not — and because
    seeing them labelled is what lets a person notice the one they never asked for.
    """

    text: str
    kind: str = "do"


class RequirementsRequest(BaseModel):
    """Extract a task's requirements without running anything. One model call, no tools."""

    task: str


class RequirementsOut(BaseModel):
    items: list[RequirementOut]
    note: str = ""
    """"" on success; a short message when the extraction degraded, in place of a 500."""


# --- runs (autonomous run receipts) ---------------------------------------------------------------


class FileDiffOut(BaseModel):
    path: str
    patch: str  # a unified-diff body (@@ hunks, +/- lines) — the real change this attempt made
    truncated: bool  # the patch was clipped to the char bound


class AttemptReceiptOut(BaseModel):
    index: int
    verified: bool  # executable evidence passed for this attempt
    reverted: bool  # the workspace was rolled back after this attempt failed
    success: bool
    verify_output: str  # the concrete verifier output (test/assert), truncated
    diff_summary: str  # what this attempt actually changed in the workspace, audited before any revert
    feedback: str  # the retry feedback this attempt produced, truncated
    diffs: list[FileDiffOut]  # real per-file unified diffs (on a reverted attempt: what it ATTEMPTED)
    evidence: str  # who approved: "verifier" | "diff+manager" | "diff" | "manager" | "none"
    # null = could not be measured, which is NOT false ("measured, nothing changed"). The UI must
    # render the unknown as its own state; deriving it by absence is how the third state decays.
    diff_productive: bool | None
    side_effects: list[str]  # out-of-checkout effects performed (send_email, http_post, …)
    system_sha: str = ""
    """The fingerprint of the system message this attempt ran under (twelve hex characters), the
    same value its trace line carries. Empty for a receipt written before the field existed.

    On the wire so a change in what a run did can be set beside a change in what it was told: two
    attempts with different values were not given the same instructions, whatever else they
    share."""
    truncated_steps: int | None = None
    """How many of this attempt's model calls the provider cut at the output ceiling. ``null`` on
    a receipt written before the field existed — not recorded, which is not zero."""
    dropped_tool_calls: int | None = None
    """Tool calls the gateway dropped from this attempt because their arguments did not parse. A
    step whose every call was dropped reads as a final answer; this says something was asked for."""


class RunReceiptOut(BaseModel):
    ts: str  # ISO-8601 UTC timestamp of the run's completion
    chimera_version: str = ""
    chimera_git_sha: str = ""
    task: str  # the task text, truncated
    success: bool
    paused: bool  # interrupted for human approval (paused runs aren't persisted; false in practice)
    verify_command: str | None  # the shell command that judged the run, or null (no verifier)
    answer: str  # the final answer, truncated
    attempts: list[AttemptReceiptOut]  # the per-attempt verify-or-revert proof trail

    stopped_reason: str = ""
    """Why the loop stopped: ``final`` | ``max_steps`` | ``tool_loop`` | ``budget`` | ``spend`` |
    ``cancelled`` | ``handover``. Empty for a receipt written before the field existed.

    On the wire because a Runs list without it renders three different endings as one: the run the
    user cancelled, the run the dollar ceiling cut off, and the run whose work the verifier rejected
    all carry ``success: false`` and are otherwise indistinguishable on screen. The chat turn's
    badge already makes this distinction from the ``done`` frame; the durable list could not."""

    ending: str = "unknown"
    """How the solve loop ended: ``success`` | ``no_op`` | ``exhausted`` | ``cancelled`` | ``spend``
    | ``paused`` | ``denied`` | ``handover``. ``unknown`` for a receipt written before the field
    existed.

    On the wire for the reason the field above gives and does not finish. ``stopped_reason`` is the
    *turn* loop's word, and the solve loop writes it at three sites only — so the run that used up
    its attempts, the one whose answer a person refused, and the one that succeeded while changing
    nothing on disk still arrive here as the same blank. This is the one field that separates them,
    and it is set at every return rather than at the interesting ones."""

    stagnant: bool | None = None
    """Were the failures repeating when it ended? ``null`` = nobody looked — no detector was
    configured, or the run stopped at an ending that does not summarise its failures — and that is
    not ``false``. Reported beside the ending, never as the ending: no part of the loop stops on
    stagnation, so an ``ending`` of ``stalled`` would name a cause the code does not have."""

    workspace: str = ""
    """The project this run happened in. Empty for a receipt written before the field existed.

    On the wire so a screen can label a row rather than only filter by it: a Runs list that has been
    narrowed to one project and a Runs list that happens to contain one project look identical, and
    the reader is the one who has to tell them apart."""

    report_defects: list[dict[str, str]] = Field(default_factory=list)
    delivered_matches_verified: bool | None = None
    """Is the tree on disk still the one the winning attempt's verdict was about?

    On the wire because ``success`` is read as a statement about the delivery while it is a statement
    about an instant, and the two came apart in a measured run: the row said verified with the
    verifier's own ``Ran 20 tests ... OK`` beside it, and the delivered files failed all twenty.

    ``null`` means nothing looked — no successful attempt, no workspace guard, or a receipt written
    before the check existed — and is deliberately not ``true``, so the screen does not stamp the
    stronger claim on every row already in the file."""


class CancelOut(BaseModel):
    ok: bool  # True when the run_id was known and its stop flag was set; False for a finished/unknown id
    # (a no-op — cancellation is COOPERATIVE: the run halts before its NEXT attempt, never mid model-call)


class PausedRunOut(BaseModel):
    """A run that stopped before finalizing and is waiting for a human verdict."""

    thread_id: str  # its durable identity — the handle for POST /api/runs/{thread_id}/respond
    answer: str  # what the run WOULD have finalized; the thing being sanctioned or rejected
    tainted: bool  # True when the pause is because the run consumed untrusted content


class HitlRequest(BaseModel):
    """A human verdict on a paused run — the LangGraph ``HumanInterrupt`` envelope."""

    action: str  # accept | edit | respond | ignore
    answer: str | None = None  # for "edit": the corrected answer to finalize INSTEAD of the model's
    feedback: str | None = None  # for "respond": guidance fed back so the run tries again


class HitlOut(BaseModel):
    ok: bool  # False when the thread is unknown, not awaiting approval, or the action is unrecognised
    # (a no-op, 200 — a verdict on a run that already resolved is exactly what a stale click sends)
    resume_required: bool  # True whenever ok. Recording the verdict does not conclude the run: EVERY
    # action (accept included) needs the client to re-POST /api/runs with the same thread_id, which is
    # where the answer is finalized, the receipt written and the checkpoint cleared.
    retries: bool  # True only for "respond": that resume makes ANOTHER attempt with the feedback.
    # accept/edit/ignore conclude on the reviewed output without re-running the worker.


class SettingChangeOut(BaseModel):
    """One setting a suggestion would change: what it holds now, and what was proposed."""

    key: str
    current: str
    proposed: str


class SettingsSuggestionOut(BaseModel):
    """A settings change the desktop bridge suggested (`governance/setting_suggestions.py`).

    Nothing has been written: the owner's yes on this card is what writes it, and only if every key
    still holds ``current`` when the yes arrives."""

    changes: list[SettingChangeOut]
    suggested_by: str  # the surface — `desktop_bridge`
    client_hint: str = ""  # which bridge token: its last four characters, as Settings shows it
    expires_at: float  # server epoch seconds; past it the card is retired as a timeout
    digest: str = ""
    """A hash of everything above. The screen sends it back with a yes (``ApprovalAnswerIn.digest``):
    a card whose file changed after it was shown is not applied."""


class ApprovalOut(BaseModel):
    """One question waiting for a person, written by `pending.ask_durably` from an attended surface."""

    kind: str = ""
    """Empty for a question a tool call is parked on; ``settings_suggestion`` for a settings change
    waiting for the owner, whose ``suggestion`` says exactly what would change."""

    suggestion: SettingsSuggestionOut | None = None

    #: Which turn asked, which conversation it belongs to, and in which folder: what a card needs to
    #: say where it comes from, with several conversations working at once. Empty when unknown (a
    #: surface that names no turn, or a turn already gone). ``work`` is a background work's title.
    run_id: str = ""
    session_id: str = ""
    workspace: str = ""
    work: str = ""

    id: str
    action: str  # `<tool>: <command | path | url>` — empty only on a question raised before 0.54
    reason: str
    asked_at: float
    age_seconds: float
    decision: str = "review"  # the level of the verdict that raised it: block | review | warn
    p: float | None = None
    """The calibrated probability that raised the question, when the REVIEW band produced it.

    ``None`` for a question a lexical rule or the taint ledger raised — those have no number, and a
    card that rendered ``p=0.00`` for them would be inventing one. The card shows the number only
    when it is here; the record writes the column only then too."""

    band: str = ""
    """Which band of the REVIEW band it fell in — ``review`` | ``uncertain`` | ``allow`` |
    ``uncalibrated`` | ``halt`` | ``gate`` | ``none``. Empty when no band was consulted.

    Sent beside ``p`` because the two are only readable together: 0.45 is a confident ALLOW below
    ``allow_below`` and an uncertain one between the thresholds, and the card is where a person has
    to read both at once to answer."""

    decider_model: str = ""
    """The build that answered, when a model did — ``qwen3:4b@Q4_K_M``, the dated vendor build.

    On the wire because the map is keyed on the build (study 21 §2ad): the same 0.80 means different
    things under different builds, so a probability whose model is not named is a probability about
    nothing in particular."""

    decision_id: str = ""
    """The decision log's id for the answer that raised the question. The card posts the person's
    *was this dangerous?* to ``/api/decisions/{decision_id}/label``; empty for a rule-raised question,
    and the card then does not ask."""


class ApprovalAnswerIn(BaseModel):
    approved: bool
    digest: str | None = None
    """For a settings suggestion: the ``suggestion.digest`` the card was drawn from. A yes without
    it, or with one the card no longer matches, is ``changed`` and writes nothing."""


class ApprovalAnswerOut(BaseModel):
    ok: bool  # False when no question with that id is waiting — a stale click, 200, not a 404
    outcome: str | None = None
    """For a settings suggestion only: ``applied`` | ``refused`` | ``stale`` (a key no longer holds
    the value the card showed) | ``changed`` (the card's file changed after it was shown) |
    ``invalid`` (the value fails a check now) | ``expired``. Only
    ``applied`` wrote anything. Absent for every other question, whose answer stays ``{ok}``."""

    detail: str | None = None
    """The keys applied, the keys that moved, or the check that refused — for the sentence the
    screen shows. Never a value."""


class DecisionLabelIn(BaseModel):
    event: bool
    """Whether the question's event was true — for ``governance.danger``, whether the action WAS
    dangerous. Not whether it was approved: a person approves a dangerous action they meant to run."""


class DecideQuestionIn(BaseModel):
    type: Literal["noul", "choice", "score"]
    instructions: str | dict[str, Any] | list[Any] | None = None
    """Text, or an object/list rendered as JSON; optional on a noul whose criteria say it."""
    criteria: dict[str, str | dict[str, Any] | list[Any] | None] | list[str | dict[str, Any] | list[Any]] = {}
    """noul: ``true``/``false``; choice: option -> meaning (``null`` = the name alone), in order; score:
    the SDK's list of level meanings, lowest first, or level -> meaning."""


class DecideIn(BaseModel):
    """The open System One request (study 22, phase 4) — the vendor SDK's own shape (study 24, A1)."""

    state: str | dict[str, Any] | list[Any]
    questions: dict[str, DecideQuestionIn]
    decision: str = ""
    """Optional name the calibration map is found by; empty = ad hoc, never calibrated."""


class DecideOut(BaseModel):
    model: str
    answers: dict[str, dict[str, Any]]
    """Per key, each with ``type``: ``{"noul": p}`` | ``{"choice", "probabilities", "confidence"}`` |
    ``{"score", "probabilities", "legend": {level: meaning}, "confidence"}`` | ``{"error"}``."""
    receipts: dict[str, dict[str, Any]]


class DecisionSpecOut(BaseModel):
    name: str
    escalation: str
    mode: str
    bench: str
    threshold: float | None = None
    surfaces: list[str] = []
    description: str = ""


class ReliabilityBinOut(BaseModel):
    lo: float
    hi: float
    n: int
    mean_p: float | None = None
    observed: float | None = None


class DecisionGroupOut(BaseModel):
    """One instrument — decision, backend, model, wording, build — and what its log holds."""

    decision: str
    backend: str
    model: str
    prompt_hash: str
    resolved_model: str
    answers: int
    halts: int
    cached: int
    calibrated: int
    regions: dict[str, int]
    review_per_100: float | None = None
    labelled: int
    positives: int
    labelled_by_region: dict[str, int]
    catch: list[int] | None = None
    false_refusal: list[int] | None = None
    brier: float | None = None
    ece: float | None = None
    reliability: list[ReliabilityBinOut]


class DecisionRowOut(BaseModel):
    id: str
    at: float
    decision: str
    p: float | None = None
    calibrated: bool = False
    choice: str | None = None
    halt: str = ""
    cached: bool = False
    state: str = ""
    label: int | None = None
    source: str | None = None


class DecisionAlertOut(BaseModel):
    """A drift alarm computed from the log alone (``chimera/decisions/drift.py``). It annotates and
    gates nothing; ``detail`` carries the numbers the screen words it from."""

    kind: str
    decision: str
    backend: str
    model: str
    prompt_hash: str
    detail: dict[str, Any]


class DecisionsOut(BaseModel):
    """The Decisions screen: the declared decision points, what each instrument's log holds, and the
    latest answers with their labels (study 22, phase 4)."""

    review_at: float
    allow_below: float
    specs: list[DecisionSpecOut]
    groups: list[DecisionGroupOut]
    recent: list[DecisionRowOut]
    alerts: list[DecisionAlertOut] = []


class SystemOneModelOut(BaseModel):
    """One model OpenRouter lists with ``output_modalities: decisions`` (``chimera/decisions/system_one.py``)."""

    slug: str
    name: str
    input_per_m: float | None = None
    """USD per 1M input tokens; null when the index quotes none — never read as free."""
    context: int | None = None
    description: str = ""
    contract: str
    """``jev`` (Noul, Choice, Score — what Chimera's client sends), ``behavior`` or ``unknown``."""
    questions: list[str]
    alias: bool = False
    selectable: bool
    refusal: str = ""
    """Why it cannot be chosen, as a word: ``alias`` | ``behavior_contract`` | ``unknown_contract``."""
    calibrated: bool = False
    """A calibration map exists for this slug; without one its confidence reads raw."""


class SystemOneModelsOut(BaseModel):
    backend: str
    model: str
    """As configured; empty = the backend's default, which ``default_model`` names."""
    default_model: str
    backends: list[str]
    models: list[SystemOneModelOut]
    stale: bool = False
    """True when the index was not reached and ``models`` is the shipped default alone."""
    reason: str = ""
    openrouter_key_set: bool = False
    """Whether an OpenRouter key is configured — the System One backend halts every call without one."""


class DecisionLabelOut(BaseModel):
    ok: bool  # False when the log has no answer with that id — a stale card, 200, not a 404


class BatchCancelOut(BaseModel):
    ok: bool  # True when the batch_id was known AND the request named real task(s); False for a
    # finished/unknown batch or an out-of-range index (a no-op, 200 — never a 404)
    cancelled: int  # how many task stop flags this call actually RAISED (already-stopping tasks don't
    # recount). Cancellation is COOPERATIVE: each task halts before its NEXT attempt, never mid
    # model-call — so this is a count of requests made, not of workers already stopped.




# --- agents (a parallel batch of isolated autonomous runs — the Agent Manager) --------------------
# The terminal ``batch_done`` shape of ``POST /api/agents``. SSE can't carry a ``response_model``, so
# these are surfaced to OpenAPI (and the generated TS types) via ``GET /api/agents/schema`` — exactly
# how ``RunReceiptOut`` reaches the schema through ``GET /api/runs``. Every field is real, already-
# computed evidence: the per-task ``AutonomousResult`` (success/attempts/reverted + its real per-file
# diffs, via ``build_receipt``) and the worktree merge outcome (``conflicts``/``merged``/``is_repo``).


class AgentResultOut(BaseModel):
    index: int  # the task's position in the request (0-based) — tags this task's live event frames
    task: str  # the task text, truncated
    success: bool  # the AutonomousResult's real success flag (verify-or-revert passed)
    attempts: int  # how many verify-or-revert attempts this task took
    reverted: bool  # any attempt was rolled back after verification failed
    changed_paths: list[str]  # files this task's worktree changed (merged back unless a conflict)
    diffs: list[FileDiffOut]  # the terminal attempt's real per-file unified diffs (never fabricated)
    error: str  # why this task produced NO result: "timed out after Ns" (the batch's wall-clock
    # deadline blew while this task was still running) or the exception that killed the unit. Empty
    # whenever the task actually ran — a task that ran and did not pass says so through ``success``,
    # and the two must not look alike on screen.


class AgentsBatchOut(BaseModel):
    results: list[AgentResultOut]  # one per task, in request order
    conflicts: list[str]  # files ≥2 successful tasks BOTH changed — left UNMERGED, surfaced, never hidden
    merged: int  # changed files copied back to the real workspace across all tasks
    is_repo: bool  # the workspace is a git repo — isolation is REAL only then (else in-place, no isolation)


# --- filesystem (read-only tree + file viewer for the Code screen) --------------------------------


class FsNodeOut(BaseModel):
    name: str  # the entry's base name
    path: str  # its path relative to the workspace (POSIX), the key to expand/open it
    is_dir: bool


class FsTreeOut(BaseModel):
    workspace: str  # the resolved workspace root this listing is scoped to
    path: str  # the (relative) directory listed
    entries: list[FsNodeOut]  # immediate children only (dirs first, then files, alphabetical)
    capped: bool  # the listing hit the max-entries cap (some children are omitted)


class SearchHitOut(BaseModel):
    """One matching line."""

    path: str  # workspace-relative, forward slashes — the shape the tree and the editor already use
    line: int  # 1-based
    text: str  # the matching line, clipped
    start: int  # where the match begins in `text`, so the UI highlights rather than re-searches
    end: int


class SearchOut(BaseModel):
    """What a search found, and how honestly it found it."""

    hits: list[SearchHitOut]
    #: "ripgrep" or "python". Reported because they are not equivalent — the fallback is slower and
    #: ignores `.gitignore`. A silent fallback would be useful and dishonest; naming it is what lets
    #: the screen say the search was the simpler one.
    engine: str
    capped: bool  # the hit cap stopped it early — a capped result that looks complete is a lie
    timed_out: bool  # ran out of time, which is NOT the same as "too many answers"
    elapsed_ms: int
    error: str  # empty on success; never a traceback


class FsFileOut(BaseModel):
    path: str  # the (relative) file read
    content: str  # UTF-8 text, truncated at the read cap; empty for a binary/dir/missing file
    truncated: bool  # the content was clipped at the read cap
    note: str  # a short honest note ("binary or non-text", "not found") or "" for a clean read
    #: ``pdf``/``docx``/``xlsx``/``pptx`` when ``content`` is a TEXT PREVIEW of that document
    #: (study 29, P6.3) — never editable, since saving it would replace the document with its text.
    #: Empty for every other file.
    document: str = ""


class FsFileWrittenOut(BaseModel):
    path: str  # the (relative) file written
    bytes: int  # bytes actually written to disk (may exceed content length on a CRLF-preserved file)


# --- git (status / diff / commit / scoped revert for the Code screen's git panel) ------------------


class GitFileOut(BaseModel):
    path: str  # the changed file's path relative to the repo root (rename → the new name)
    x: str  # the index (staged) status char from porcelain XY (" " when unstaged)
    y: str  # the worktree status char from porcelain XY (" " when the change is only staged)
    staged: bool  # the change is present in the index (x is a real status and it isn't untracked)
    untracked: bool  # git doesn't track this file yet (porcelain "??")


class GitStatusOut(BaseModel):
    is_repo: bool  # False when the folder isn't a git repo (or git is missing) — the honest empty-state
    branch: str  # the current branch ("" when not a repo, or detached/no-commits-yet edge cases)
    files: list[GitFileOut]  # changed files (empty when the tree is clean)


class GitUncommittedOut(BaseModel):
    is_repo: bool  # False when the folder isn't a git repo (or git is missing): nothing is known
    files: list[str]  # the asked-about files git still reports as changed, workspace-relative, in order


class GitDiffOut(BaseModel):
    is_repo: bool  # False when the folder isn't a git repo (or git is missing)
    patch: str  # the real unified-diff body (@@ hunks, +/- lines); "" when there's no diff


class GitCommitOut(BaseModel):
    ok: bool  # True only after a real, non-zero-free `git commit` — explicit paths staged, never add -A
    commit: str  # the short HEAD hash on success; "" otherwise
    output: str  # the combined git stdout+stderr, truncated
    error: str | None  # a short git error when ok is False; null on success


class GitInitOut(BaseModel):
    ok: bool  # True when the folder is a repo afterwards (an empty folder counts — nothing to snapshot)
    commit: str  # the short hash of the snapshot commit; "" when there was nothing to commit
    output: str  # the combined git stdout+stderr, truncated
    error: str | None  # a short git error when ok is False; null on success


class GitRevertOut(BaseModel):
    ok: bool  # True when the scoped revert completed (git checkout + clean on the passed paths)
    reverted: list[str]  # the paths the revert was scoped to (echoed back on success)
    error: str | None  # a short git error when ok is False; null on success


class PullRequestReadinessOut(BaseModel):
    """What opening a pull request from the workspace would push, or the first reason it cannot.

    ``reason`` is a word the screen translates (``chimera.core.pull_request.REASONS``); empty when
    ``ready``. Nothing here is a credential: ``remote`` is origin's PUSH URL (where the push goes)
    with any credential in it replaced by ``***``, and gh is asked for its exit code only."""

    ready: bool
    reason: str
    is_repo: bool
    branch: str
    base: str
    head: str  # the full hash that would be pushed; sent back with the request, pushed by hash
    remote: str
    remote_head: str  # the commit the branch is at on origin now; "" when the push creates it
    ahead: int
    commits: list[str]  # "<short hash> <subject>", newest first, at most 20
    diffstat: str
    uncommitted: int  # changed files the push will NOT carry
    gh: bool
    gh_signed_in: bool


class PullRequestOut(BaseModel):
    ok: bool
    url: str  # the pull request, when gh printed one
    output: str  # git's and gh's output, credentials removed, truncated
    error: str | None


# --- governance / security (injection red-team scoreboard + audit log) -----------------------------


class InjectionCategoryOut(BaseModel):
    category: str  # destructive | backdoor | exfil | self_modify
    defended_asr: float  # attack-success-rate for this category WITH defenses (lower = better)
    undefended_asr: float  # same category WITHOUT defenses — the baseline the defenses improve on
    count: int  # number of attacks in this category


class InjectionAttackOut(BaseModel):
    id: str
    category: str
    harmful_tool: str  # the tool the attacker wanted invoked
    blocked_defended: bool  # the defenses stopped it
    blocked_undefended: bool  # it was stopped even bare (baseline) — false for every real attack


class InjectionReportOut(BaseModel):
    total_attacks: int
    defended_asr: float  # fraction of harmful calls that still execute WITH the defenses
    # WITHOUT the defenses this is 1.0 by construction — an unwrapped tool always runs — so it is the
    # definitional floor this layer is compared against, NOT a measured baseline system. Both figures
    # assume an agent that is already injected; neither says how easily a model gets injected.
    undefended_asr: float
    defended_block_rate: float
    undefended_block_rate: float
    by_category: list[InjectionCategoryOut]
    attacks: list[InjectionAttackOut]
    leaks_defended: list[str]  # attack ids that get through EVEN defended — the named honest gap
    defense: str = "taint_narrowing"
    """Which layer these numbers are about. One layer, named, so the score cannot be read as a
    verdict on the whole stack."""
    armed: bool = True
    """Whether that layer is switched ON in this install (CHIMERA_TAINT_NARROW). False means the
    defended column describes a build the reader does not have."""
    trust_kernel: bool = False
    # The cost half. Every figure above is what the layer BLOCKS; these are what it REFUSES of the
    # honest work that trips the same surface — the number `bench/injection` registered as the gate
    # this scoreboard used to omit, and the reason a good defended score was never the whole story.
    legitimate_tasks: int = 0
    over_block_rate: float = 0.0  # refused / legitimate, with no one to ask (the unattended floor)
    over_block_workspace: float = 0.0  # control: work that read only its own repo — must stay 0
    over_block_fetch: float = 0.0  # work that read something external first — where the cost lives
    over_block_with_approver: float = 0.0  # the same rows when the person approves what they asked for
    questions_asked: int = 0  # how many of the legitimate rows would become a question, attended
    pending_questions: int = 0  # questions waiting right now in this install (`GET /api/approvals`)
    """Whether these numbers cover the BLOCK/REVIEW policy rules. They never do — the suite
    exercises taint narrowing only — so this is a constant, and deliberately not derived from
    `CHIMERA_GOVERNANCE`. Where the rules RUN is a different question with a different answer per
    endpoint, and a single boolean that tried to answer it would have gone quiet for the four HTTP
    surfaces that still have no kernel. Said out loud, because a good score invites the reader to
    assume every layer they have heard of is behind it."""


class AuditEventOut(BaseModel):
    seq: int
    type: str
    summary: str  # a short human string flattened from the entry's remaining (arbitrary) keys


class AuditChainOut(BaseModel):
    """Whether the log's own tamper-evidence holds. Reported because nothing used to ask."""

    ok: bool  # False for a broken link, or a log short of / rewritten past its anchor — never for empty or legacy
    checked: int  # entries whose digest was verified
    unchained: int  # legacy entries with no digest: cannot be verified either way, never "failed"
    broken_at: int | None  # index of the first entry that does not hold
    reason: str


class GovernanceAuditOut(BaseModel):
    events: list[AuditEventOut]  # newest-first (highest seq first)
    count: int
    populated: bool  # False when the audit file has no entries — drives the honest empty-state
    chain: AuditChainOut


# --- memory layers (by-kind + provenance + by-source view) ----------------------------------------


class MemoryLayerOut(BaseModel):
    kind: str  # working | episodic | semantic | persona (+ any unknown kind, folded in trailing)
    count: int
    clean: int  # items in this kind with provenance "clean"
    tainted: int  # items in this kind with provenance "tainted" (shown as "unverified")


class MemorySourceOut(BaseModel):
    source: str  # origin app (e.g. "chimera", "hermes"); "" when blank (UI shows "—")
    count: int


class MemoryLayersOut(BaseModel):
    total: int
    clean: int  # overall items with provenance "clean"
    tainted: int  # overall items with provenance "tainted"
    layers: list[MemoryLayerOut]  # ALWAYS the 4 canonical kinds (0-count included) + any unknown
    by_source: list[MemorySourceOut]  # top 20, count desc
    # Pass-through of settings.semantic_memory (opt-in, off by default). A boolean flag for an honest UI
    # note only — NOT an embeddings index count; no such index exists when it is False.
    semantic_embeddings_enabled: bool


# --- tools (agent tool registry inventory) --------------------------------------------------------


class ToolInfoOut(BaseModel):
    name: str
    description: str
    params: list[str]  # the tool's parameter NAMES (parameters.properties keys); [] when it takes none
    tags: list[str]  # capability tags derived purely from the tool NAME vs the governance sets, in a
    # stable order: network / read / write / exec / side-effect. [] when the name is in none of them.
    untrusted_output: bool  # True only for MCP/OpenAPI-imported tools; False for native tools (read as-is)


class UnavailableToolOut(BaseModel):
    """A tool the registry holds only under a condition that is not met right now."""

    name: str
    description: str
    kind: Literal["setting", "key", "package"]
    variables: list[str]
    """``setting``: the variable the screen writes ``"1"`` to. ``key``: the credential(s) it needs."""
    requires: str = ""
    switchable: bool
    """Only a ``setting`` can be turned on from the screen; a key or a package cannot be invented."""
    default_on: bool = False
    """A setting that is on unless the owner switched it off — absent means someone turned it off."""
    in_settings: bool = False
    """Every variable in ``variables`` can be saved from the Settings screen (``is_editable``). False
    for the SMTP/IMAP/ICS rows, which live in ``.env`` only — the screen must not send anyone to a
    field that does not exist. Defaults False so an older server never earns a "Settings" claim."""


class DeferSavingHalfOut(BaseModel):
    """What deferral would do to one half of the schema, measured on this install.

    Characters of JSON schema, not tokens: the ratio is what matters, and the two modules that
    measure it chose characters to avoid a tokenizer dependency. ``saving_pct`` is NEGATIVE when the
    three proxies cost more than the tools they replace, which below a handful of tools they do.
    """

    tools: int
    deferred: int
    declared_chars: int
    deferred_chars: int
    saving_pct: float


class DeferSavingOut(BaseModel):
    """``GET /api/tools/defer-saving`` — the token half of the two deferral switches, measured here.

    Only the token half. Whether a model still finds a deferred tool is the other half, and the
    built-in bench was inconclusive on it (`bench/tool_defer/RESULT.md`).
    """

    builtin: DeferSavingHalfOut
    mcp: DeferSavingHalfOut | None = None
    #: Why ``mcp`` is null when it is: autoload off, servers not connected yet (they connect on the
    #: first conversation, and this read never connects them), no server connected, or a connected
    #: server failed to answer its tool listing (the built-in figure is still reported).
    mcp_state: Literal["measured", "autoload_off", "not_connected", "no_servers", "unavailable"]


class ToolsOut(BaseModel):
    tools: list[ToolInfoOut]
    count: int
    unavailable: list[UnavailableToolOut] = []
    """Conditional tools absent right now, with what would turn each on (`chimera/tools/conditional.py`)."""


# --- MCP / Integrations (configured servers + live test) ------------------------------------------


class McpLastTestOut(BaseModel):
    """The remembered outcome of the last Test of a server — history, not a live state.

    Kept so the screen can say "tested at 14:02, 4 tools" after a relaunch. It must never be shown
    as "connected": a test from yesterday says nothing about whether the server starts today.
    """

    ok: bool
    tool_count: int
    tested_at: float  # unix seconds


class McpServerOut(BaseModel):
    name: str
    command: str
    args: list[str]
    env_keys: list[str]  # env variable NAMES only — the secret VALUES are never returned
    # Null when never tested, or when the config changed since (an edit forgets the old result).
    last_test: McpLastTestOut | None = None


class McpServersOut(BaseModel):
    servers: list[McpServerOut]
    count: int


class McpToolOut(BaseModel):
    name: str
    description: str


class McpTestOut(BaseModel):
    ok: bool  # True ONLY after a real stdio connect + tool enumeration — the sole "connected" signal
    tools: list[McpToolOut]  # the tools the server exposed on a successful connect; [] otherwise
    error: str | None  # a short, secret-free failure message when ok is False; null on success
    # "It works" and "the agent can use it" are different facts, and only reporting the first is how
    # a server that tested green sat unreachable through a nineteen-minute run. False means a run
    # started now gets none of these tools; null means the answer could not be read.
    reaches_agent: bool | None = None
    # WHY not, as an enum the app translates: "autoload_off" (the toggle is off, so no run is given
    # these tools) or "added_after_connect" (the servers are connected once per process and this one
    # arrived later, so it needs a restart). The remedies differ, which is why one flag is not
    # enough. An enum rather than a sentence because the app ships in ten languages. Null whenever
    # `reaches_agent` is not False.
    reaches_agent_reason: str | None = None


class McpAddRequest(BaseModel):
    name: str
    command: str
    args: list[str] = []
    env: dict[str, str] = {}  # accepted on write (stored locally), never echoed back in reads


# --- maturity (self-eval coverage scorecard by surface) -------------------------------------------


class MaturitySurfaceOut(BaseModel):
    name: str  # the surface (fusion, evolution, governance, memory, benchmarks, resilience, interop)
    proven: int  # coverage-IDs whose evidence test-file exists (presence, NOT that it passes)
    total: int  # coverage-IDs that constitute the surface
    ratio: float  # proven/total, 0..1
    level: str  # present (>=0.9) / partial (>=0.5) / sparse — share of test FILES present, not a grade
    missing: list[str]  # coverage-IDs with no evidence test-file yet (the honest gap)


class MaturityWeakestOut(BaseModel):
    name: str  # the surface with the lowest coverage — the evolution loop's next objective
    ratio: float


class MaturityOut(BaseModel):
    available: bool  # False when neither a live test suite nor the shipped snapshot could be read
    source: str | None  # "live" (globbed the real tests dir) | "snapshot" (shipped fallback) | null
    proven: int  # overall coverage-IDs proven across all surfaces
    total: int
    ratio: float
    level: str
    surfaces: list[MaturitySurfaceOut]
    weakest: MaturityWeakestOut | None  # null when every surface is fully proven
    generated_for: str | None  # the chimera version a snapshot was generated for (null when unavailable)


# --- benchmarks (the app's REAL, recorded performance numbers — honestly framed) -------------------


class BenchmarkDiscordantOut(BaseModel):
    treatment_only: int  # tasks Chimera passed that the bare model failed (the lift's real evidence)
    baseline_only: int  # tasks the bare model passed that Chimera failed


class BenchmarkLiftOut(BaseModel):
    suite: str  # the suite label — makes clear this is the internal suite, NOT SWE-bench/Terminal-Bench
    model: str  # the cheap/weak model both arms ran (Chimera's lift is over the SAME model, bare)
    n: int  # paired-task count — the caveat travels with the number
    baseline_rate: float  # the bare model's pass rate (0..1)
    treatment_rate: float  # the model + Chimera's pass rate (0..1)
    delta: float  # treatment - baseline (0..1) — the lift, as measured
    ci: list[float]  # 95% paired CI [lo, hi] — significance is exactly "does this exclude 0"
    significant: bool  # carried verbatim from the results file; never asserted independently of the CI
    source: str  # the committed results file this block is read from
    note: str  # the honest one-liner about THIS run (scope, limits, no re-rolling)


class BenchmarkArmOut(BaseModel):
    """One arm of a three-way decomposition — which component of the scaffold earned the delta."""

    arm: str  # "baseline" | "scaffold" | "scaffold+diff-gate"
    resolved: int  # instances resolved by this arm
    n: int  # instances attempted (identical across arms — the same frozen slice)
    rate: float  # resolved / n (0..1)
    precision_when_edited: float  # resolved / patches produced — the mechanism, not the headline


class BenchmarkExternalOut(BaseModel):
    benchmark: str  # the external benchmark's name (e.g. SWE-bench Verified, Terminal-Bench)
    model: str  # the model both arms ran
    n: int  # task count
    baseline_rate: float  # bare model pass rate (0..1)
    treatment_rate: float  # + Chimera scaffold pass rate (0..1)
    delta: float  # treatment - baseline (0..1); may be negative — the humbling results ship too
    ci: list[float]  # 95% paired CI [lo, hi] — significance is exactly "does this exclude 0"
    significant: bool  # carried verbatim; never asserted independently of the CI
    source: str  # the committed RESULTS.md this block is cited from
    note: str  # the honest one-liner: scope, limits, and what the number is NOT
    # Present only where a run actually decomposed the delta by component. Optional rather than
    # required because not every external result has an attribution arm — and a surface that shows a
    # headline delta without saying which part earned it invites exactly the wrong inference.
    decomposition: list[BenchmarkArmOut] | None = None


class BenchmarksOut(BaseModel):
    available: bool  # False when the shipped snapshot couldn't be read — honest empty-state, never a 500
    internal_lift: BenchmarkLiftOut | None  # the promising weak-model lift (null when unavailable)
    external: list[BenchmarkExternalOut]  # recorded external results (e.g. Terminal-Bench); [] otherwise
    generated_for: str | None  # the chimera version the snapshot was generated for (null when unavailable)


# --- OpenAPI connectors (study 29, P7.5) ----------------------------------------------------------


class ConnectorOperationOut(BaseModel):
    id: str  # the operationId in the pinned spec — what `operations` in a PATCH names
    tool: str  # the tool name the agent sees: `api_<connector>_<operationId>`
    method: str  # GET, POST, PUT, PATCH or DELETE
    path: str
    summary: str  # the spec's own words, truncated — third-party text, shown as such
    selected: bool


class ConnectorOut(BaseModel):
    name: str
    source: str  # where the spec was fetched from, once; loading never re-fetches it
    base_url: str  # the only origin a call may reach
    enabled: bool  # off when added: nothing loads until the owner switches it on
    allow_writes: bool  # off when added: without it no operation but GET can be selected
    unattended: bool  # off when added: the bots, cron, lanes and servers load it only when on
    key_env: str  # the `.env` variable NAME the key is read from; never its value
    key_envs: list[str]  # the names this connector may read: its own, or a reserved tool slot
    key_in: str  # "header" or "query"
    key_name: str  # the header or query parameter the key is sent as
    key_prefix: str  # e.g. "Bearer " — header style only
    key_set: bool
    key_hint: str  # at most the last four characters, as on the settings screen
    added_at: str
    operations: list[ConnectorOperationOut]
    problem: str  # empty when the pinned spec reads; else why this connector loads nothing


class ConnectorsOut(BaseModel):
    connectors: list[ConnectorOut]
    store: str  # the file the VPS can be configured through: `<home>/connectors.json`


class ConnectorAddIn(BaseModel):
    name: str
    source: str  # an http(s) URL (fetched through the SSRF guard) or a file on this machine
    base_url: str | None = None  # overrides the spec's own server


class ConnectorPatchIn(BaseModel):
    enabled: bool | None = None
    allow_writes: bool | None = None
    unattended: bool | None = None
    operations: list[str] | None = None
    base_url: str | None = None
    key_env: str | None = None
    key_in: Literal["header", "query"] | None = None
    key_name: str | None = None
    key_prefix: str | None = None


class ConnectorKeyIn(BaseModel):
    value: str  # write-only: stored in `.env`, never returned
