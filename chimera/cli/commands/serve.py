"""Long-running surfaces: serve, the desktop app, ACP, MCP, A2A and the messaging bots.

Moved verbatim out of ``chimera/cli/main.py`` (S30-70); ``chimera.cli.main`` re-exports every
name defined here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from chimera import __version__
from chimera.cli.commands._shared import (
    _apply_tool_allowlist,
    _cascade_backend,
    _session_profile,
    app,
    console,
    owner_identity,
)
from chimera.cli.commands.memory import _memory_manager, _recall_graph
from chimera.cli.commands.ops import _cron_store
from chimera.config import get_settings
from chimera.governance import governed_profile

if TYPE_CHECKING:
    from chimera.config import Settings
    from chimera.memory import MemoryGraph, MemoryManager
    from chimera.providers import SupportsComplete
    from chimera.server import MessageGateway



@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host."),
    port: int = typer.Option(8765, "--port", help="Bind port."),
    allow_insecure_bind: bool = typer.Option(
        False,
        "--allow-insecure-bind",
        envvar="CHIMERA_ALLOW_INSECURE_BIND",
        help="Serve on a reachable address with no token. Only behind a network you already trust.",
    ),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per message."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root for tools."),
    fuse: bool = typer.Option(False, "--fuse", help="Route deep-reasoning turns through fusion."),
    no_memory: bool = typer.Option(False, "--no-memory", help="Don't recall long-term memory."),
    discord: bool = typer.Option(False, "--discord", help="Serve on Discord (needs CHIMERA_DISCORD_BOT_TOKEN + the 'messaging' extra)."),
    telegram: bool = typer.Option(False, "--telegram", help="Serve on Telegram (needs CHIMERA_TELEGRAM_BOT_TOKEN)."),
    slack: bool = typer.Option(False, "--slack", help="Serve on Slack (needs CHIMERA_SLACK_BOT_TOKEN + CHIMERA_SLACK_APP_TOKEN + the 'messaging' extra)."),
    signal: bool = typer.Option(False, "--signal", help="Serve on Signal via a signal-cli-rest-api bridge (CHIMERA_SIGNAL_API_URL + CHIMERA_SIGNAL_NUMBER)."),
    cron: bool = typer.Option(False, "--cron", help="Also run the cron daemon: fire scheduled jobs on the real clock (proactivity)."),
    cron_tick: int = typer.Option(30, "--cron-tick", help="Seconds between cron scheduler ticks."),
    mcp: bool = typer.Option(False, "--mcp", help="Serve Chimera AS an MCP server over stdio (solve/fuse/memory as tools)."),
    a2a: bool = typer.Option(False, "--a2a", help="Also expose an A2A endpoint on HTTP (agent card + task lifecycle)."),
) -> None:
    """Run the messaging gateway on HTTP, Discord, Telegram, Slack or Signal. Requires a key.

    Add ``--cron`` to also fire scheduled jobs on a real clock — turning the reactive gateway
    into an agent that acts on time (the daemon that makes proactivity real). Pass ``--mcp`` to
    instead expose Chimera *as* an MCP server on stdio, so any MCP client (Claude Desktop, an
    IDE, another agent) can call ``chimera_solve`` / ``chimera_fuse`` / ``chimera_memory_search``.
    """
    selected_platforms = [name for name, enabled in (
        ("discord", discord), ("telegram", telegram), ("slack", slack), ("signal", signal)
    ) if enabled]
    if len(selected_platforms) > 1:
        console.print("[red]Choose at most one messaging platform flag: --discord, --telegram, --slack, or --signal.[/red]")
        raise typer.Exit(code=2)

    from chimera.core import Agent, AgentConfig
    from chimera.interface import ChatSession
    from chimera.providers import LLMGateway
    from chimera.server import MessageGateway, make_server
    from chimera.tools import default_registry

    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    from chimera.server.allowlist import home_is_empty

    # Before anything below writes to home (the memory store creates memory.db): a first run read
    # after that point looks like an upgrade, and the bot would start open instead of pairing.
    home_was_empty = home_is_empty(settings)

    llm = LLMGateway()
    backend: SupportsComplete = llm
    if fuse:
        from chimera.fusion import RoutedBackend
        from chimera.fusion.factory import fusion_engine

        backend = RoutedBackend(llm, fusion_engine(llm))

    workspace_path = Path(workspace)

    if mcp:
        _serve_mcp(backend, llm, model, max_steps, workspace_path, recall=not no_memory)
        return
    cron_stop = _start_cron_daemon(backend, model, max_steps, workspace_path, cron_tick) if cron else None
    shared_memory = None if no_memory else _memory_manager()
    shared_graph = _recall_graph(shared_memory)
    shared_profile = shared_memory.profile() if shared_memory is not None else ""

    platform = (
        "discord" if discord
        else "telegram" if telegram
        else "slack" if slack
        else "signal" if signal
        else None
    )
    if platform is not None:
        adapter = _messaging_adapter(settings, platform, home_was_empty=home_was_empty)
        _serve_platform(adapter, settings, backend, model, max_steps, workspace_path, shared_memory, shared_graph)
        return

    from chimera.integrations import SendMessageTool

    push_senders = _sender_registry(settings)  # e.g. WhatsApp send, available over HTTP too
    http_send_tool = SendMessageTool(push_senders) if push_senders.platforms() else None

    def factory() -> ChatSession:
        # One registry per conversation, and the instruction is known once per TURN — so
        # `instruction=` was never a thing this surface could pass, and its ledger answered
        # `unknown` for every fetch. `unknown` is what the narrowing treats exactly as `agent`,
        # which is why `CHIMERA_TAINT_AUTHORITY` did nothing here. `on_ledger` is the seam;
        # `ChatSession.on_turn_start` is the moment. Same fix as #408's, other door.
        turn_ledger: Any = None

        def _hold(ledger: Any) -> None:
            nonlocal turn_ledger
            turn_ledger = ledger

        registry, _ = governed_profile(
            default_registry(workspace_path),
            settings=settings,
            home=settings.home,
            surface="serve",
            on_ledger=_hold,
            # Inside the kernel and the ledger, outside the fence — see `governed_profile`'s `voice`.
            voice=[http_send_tool] if http_send_tool is not None else [],
        )
        runner = Agent(
            backend,
            registry,
            # The same workspace that roots the tools. It was good enough to grant file
            # capability here and not good enough to convey the project's conventions, which is
            # incoherent: `serve --workspace X` is the headless deployment from the README, and
            # its AGENTS.md was never read.
            #
            # Not `attended`, unlike the platform bot: the webhook handler sends its jobs through
            # this same gateway, and a webhook run is unattended like a cron one. Without the step
            # wall it would have no end that anybody is there to see.
            AgentConfig(
                model=model, max_steps=max_steps, project_root=workspace_path,
                instructions=owner_identity(settings.home),
                turn_context=True,
            ),
        )
        return ChatSession(
            runner,
            memory=shared_memory,
            graph=shared_graph,
            profile=shared_profile,
            remember_from_chat=settings.remember_from_chat,
            real_history=settings.chat_real_history,
            # `None` when governance is off — the shipped default, under which `governed_profile`
            # returns before it builds a ledger and never calls `_hold`. A hook wired to nothing
            # would be a lie about what this surface has; no hook is the truth, and it also keeps
            # `ChatSession` byte-identical for the stock deployment.
            #
            # `workspace=` so a path the model gives absolutely matches the relative form the person
            # wrote, which is what `set_instruction` documents the argument for.
            on_turn_start=(
                None
                if turn_ledger is None
                else lambda message: turn_ledger.set_instruction(
                    message, workspace=workspace_path
                )
            ),
            # The same ledger learns of a tainted fact the recall hands the prompt (study 30
            # S30-25), so the narrowing arms as it would for a fetched page.
            on_tainted_recall=None if turn_ledger is None else turn_ledger.record_fetch,
            # Inbound voice/images (S30-46): the transcript is untrusted, so it enters the run's
            # ledger as a fetch nobody named — `unknown` arms the narrowing under both modes.
            on_tainted_input=(
                None
                if turn_ledger is None
                else lambda content: turn_ledger.record_fetch(
                    "inbound-media", content, requested_by="unknown"
                )
            ),
        )

    # Before anything binds. A gateway that starts and then 401s has already told the internet
    # there is a Chimera here, and still depends on every future transport remembering to ask.
    from chimera.server import InsecureBindError, check_bind

    try:
        check_bind(host, token=settings.server_token, allow_insecure=allow_insecure_bind)
    except InsecureBindError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    from chimera.server.allowlist import owner_on

    # One gateway for three routes. The WhatsApp webhook is a chat bot, so a "remember that..."
    # from a number the owner did not list is written tainted and names it (study 30 S30-29), the
    # same as on the platform bots; the HTTP `/chat` route and the scheduler's webhooks have no
    # sender to judge and the rule answers `None` for them. Read live, as the allowlist is.
    message_gateway = MessageGateway(
        factory, owner_of=lambda message: owner_on("whatsapp", get_settings(), message)
    )
    a2a_pair = _build_a2a(backend, model, max_steps, workspace_path, host, port) if a2a else None
    from chimera.scheduler.github_issue import (
        GitHubIssueJob,
        IssueJob,
        github_event_handler,
    )

    def run_github_agent(path: Path, task: str) -> Any:
        # A fresh registry rooted in THIS job's worktree. Borrowing the chat session's agent and
        # moving `config.project_root` moved nothing that writes: its file and shell tools were
        # built on `workspace_path`, so the issue's edits landed in the server's own workspace,
        # outside the ephemeral checkout, and two jobs on the pool rewrote one shared config.
        from chimera.core import Agent
        from chimera.tools import default_registry

        job_ledger: Any = None

        def _hold(ledger: Any) -> None:
            nonlocal job_ledger
            job_ledger = ledger

        registry, _ = governed_profile(
            default_registry(path),
            settings=settings,
            home=settings.home,
            surface="github-issue",
            workspace=path,
            on_ledger=_hold,
        )
        if job_ledger is not None:
            # The issue text came from whoever can open an issue: the run starts tainted.
            job_ledger.record_fetch("github-issue", task, requested_by="unknown")
        runner_agent = Agent(
            backend, registry,
            AgentConfig(
                model=model, max_steps=max_steps, project_root=path,
                instructions=owner_identity(settings.home),
                turn_context=True,
            ),
        )
        return runner_agent.run(task)

    def verify_github_issue(path: Path) -> tuple[bool, str]:
        from chimera.api.app import resolve_verify, verifier_source
        from chimera.core.verify import CommandVerifier

        verify_command, source = resolve_verify(None, path)
        if not verify_command:
            return False, "no configured verifier"
        verifier = CommandVerifier(verify_command, path, source=verifier_source(source))
        result = verifier.verify()
        return bool(result.passed and not result.abstained), source

    def publish_github_issue(path: Path, title: str, body: str) -> str:
        from chimera.tools.pull_request import OpenPullRequestTool

        # No `approve=`: the tool's default is `always_ask`, which reads CHIMERA_APPROVAL_MODE=allow
        # as ask. The push waits for the owner's yes on every job.
        return OpenPullRequestTool(path).run(title=title, body=body)

    github_runner = GitHubIssueJob(
        agent=run_github_agent,
        verify=verify_github_issue,
        publish=publish_github_issue,
        receipt_dir=Path(settings.home) / "receipts",
        receipt_callback=lambda receipt: console.print(
            f"[dim]GitHub issue job complete: {receipt.repository}#{receipt.issue_number}; "
            f"verifier={receipt.verifier_authority}; approved={receipt.push_approved}[/dim]"
        ),
    )

    def enqueue_github_issue(job: IssueJob) -> None:
        # Durable record precedes execution: an interrupted request remains available to inspect.
        queue_dir = Path(settings.home) / "github-issue-queue"
        queue_dir.mkdir(parents=True, exist_ok=True)
        queue_file = queue_dir / f"{job.repository.replace('/', '-')}-{job.issue_number}.json"
        queue_file.write_text(json.dumps(job.__dict__, sort_keys=True) + "\n", encoding="utf-8")
        github_runner.enqueue(job)

    try:
        github_secrets = json.loads(settings.github_webhook_secrets or "{}")
    except json.JSONDecodeError:
        github_secrets = {}
    github_events = github_event_handler(
        allowlist=settings.github_issue_repositories,
        secrets=github_secrets if isinstance(github_secrets, dict) else {},
        enqueue=enqueue_github_issue,
    )
    server = make_server(
        message_gateway, host, port,
        github_events=github_events,
        token=settings.server_token,
        webhooks=_webhook_handler(message_gateway),
        whatsapp=_whatsapp_webhook(settings, message_gateway, home_was_empty=home_was_empty),
        a2a=a2a_pair,
    )
    a2a_note = "  [dim]· A2A: GET /.well-known/agent.json, POST /a2a[/dim]" if a2a else ""
    console.print(
        f"[bold]Chimera gateway[/bold] on http://{host}:{port}  "
        f"[dim](POST /chat, POST /webhook/<hook>, GET /health). Ctrl+C to stop.[/dim]{a2a_note}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]stopped[/dim]")
    finally:
        server.shutdown()
        if cron_stop is not None:
            cron_stop.set()


def _bind_app_socket(host: str, port: int) -> tuple[Any, int]:
    """Bind the app's listening socket, falling back to a free port if ``port`` is taken.

    Returns the bound, **listening** socket (handed straight to uvicorn so there is no
    close-then-rebind race) and the actual port. ``port=0`` asks the OS for any free port. A fixed
    port that is already in use no longer crashes the app — it drops to an OS-assigned free port (so
    a second `chimera app`, or a Tauri sidecar, just works). No ``SO_REUSEADDR`` on purpose: on
    Windows that would let the bind succeed on a port another server already holds, defeating the
    busy-detection.

    ``listen()`` is called HERE and not left to uvicorn, because the port file — the sidecar's
    discovery channel — is written between this return and uvicorn's own ``listen()``. Measured on
    2026-09-17 (`bench/startup`): a connection attempted in that window met a bound socket that was
    not listening, and on Windows the SYN is dropped rather than refused, so the client's first
    connect sat on the 500 ms retransmit timer — a half-second of every desktop launch, paid between
    "the backend reported its port" and "the window loaded". Listening first means a connect made
    the moment the port file exists is queued in the backlog and served as soon as uvicorn accepts.
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError:
        if port == 0:  # asked for any free port and still failed — surface it
            sock.close()
            raise
        sock.bind((host, 0))  # requested port busy → OS picks a free one
    sock.listen(128)
    # The desktop sidecar binds port 0, so only the OS knows this number until now. The browser's
    # loopback check reads it from here: no declared local port may ever be the app's own API.
    from chimera.core.listeners import claim

    claim(int(sock.getsockname()[1]))
    return sock, sock.getsockname()[1]


def resolve_app_workspace(flag: str | None) -> Path:
    """Which directory the desktop app roots its tools in: flag, then ``$CHIMERA_WORKSPACE``, then cwd.

    The environment step exists for packaged builds. ``chimera app`` in a terminal inherits the
    directory you were standing in, so the cwd fallback is right by accident. A shipped desktop app
    inherits wherever its shortcut points — ``C:\\Program Files\\Chimera`` for an installed one,
    which is not the user's project and, on Windows, is not writable without elevation. The agent's
    edits then fail there in a way that reads like a bug in the agent rather than a wrong root.

    Deliberately NOT falling back to the home directory. It would be writable and it would look
    friendlier, but "the agent may edit anything under $HOME" is not a default anyone chose — and
    reach is the one setting that must be arrived at deliberately. An empty, visible, wrong-looking
    root that the user corrects beats a large, plausible-looking one they never agreed to.
    """
    return Path(flag or os.environ.get("CHIMERA_WORKSPACE") or ".")


def _kernel_observes_unless_told_otherwise() -> None:
    """The desktop starts with the trust kernel in ``observe`` when nobody chose a mode.

    The kernel shipped ``off`` on every surface, and on the desktop that meant the product's
    advertised defence — the curl-into-a-shell rule, the force-push rule, the secret-in-a-write
    rule — judged nothing on the screen most people use, while the Security screen showed an audit
    log the kernel was not writing. ``observe`` writes that log and refuses the fixed signatures
    (a BLOCK is applied in every mode that installs the kernel at all — `governance/profile.py`);
    it approves every REVIEW, so it asks nothing and stops no work. ``enforce``, which asks with a
    card since 0.58.0, stays a choice.

    A choice already made wins, wherever it was made: a value in the environment or in the home's
    ``.env`` is in ``model_fields_set`` and is left alone. Only the shipped default is replaced —
    and it is replaced in the process environment, so the Settings screen shows ``observe``, a
    PATCH to ``off`` writes ``.env`` and is honoured at the next launch, and ``chimera serve``, the
    CLI and the VPS are untouched: this is the desktop's default, not the package's.
    """
    from chimera.config import get_settings

    if "governance_mode" in get_settings().model_fields_set:
        return
    os.environ["CHIMERA_GOVERNANCE"] = "observe"
    get_settings.cache_clear()


@app.command(name="app")
def desktop_app(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host (localhost by default)."),
    port: int = typer.Option(8765, "--port", help="Bind port (0 = any free port; a busy port falls back to free)."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per message."),
    workspace: str = typer.Option(
        None, "--workspace", "-w",
        help="Workspace root for tools. Falls back to $CHIMERA_WORKSPACE, then the current "
             "directory — which is the wrong root for a packaged app, whose current directory is "
             "wherever its shortcut points.",
    ),
    fuse: bool = typer.Option(False, "--fuse", help="Route turns through fusion (no token streaming)."),
    no_memory: bool = typer.Option(False, "--no-memory", help="Don't recall long-term memory."),
    cron: bool = typer.Option(
        None,
        "--cron/--no-cron",
        help="Fire scheduled jobs while the app is open (proactivity). Default: the CHIMERA_APP_CRON "
        "setting (on). --no-cron makes the app purely reactive.",
    ),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the app in your browser."),
    emit_port_file: str = typer.Option(
        None,
        "--emit-port-file",
        help="Write the final http://host:port URL to this file once bound (for a parent/sidecar).",
    ),
) -> None:
    """Run the Chimera Desktop app: the HTTP+SSE API + the built React UI (needs the 'desktop' extra).

    Serves the same-origin SPA (``apps/desktop/dist``) and a streaming chat API over the real agent
    stack. Install with ``pip install 'chimera-agent[desktop]'`` and build the UI once with
    ``npm --prefix apps/desktop run build``.
    """
    try:
        import uvicorn

        from chimera.api import build_api_app
    except ImportError:
        console.print(
            "[red]The desktop app needs the 'desktop' extra.[/red]\n"
            "  pip install 'chimera-agent[desktop]'"
        )
        raise typer.Exit(code=1) from None

    from chimera.core import Agent, AgentConfig
    from chimera.core.instructions import load as load_identity
    from chimera.core.instructions import render as render_identity
    from chimera.interface import ChatSession
    from chimera.providers import LLMGateway
    from chimera.tools import default_registry

    _kernel_observes_unless_told_otherwise()
    settings = get_settings()
    # One server per data folder, claimed before anything is built. A server keeps in memory the
    # turns it runs, the folder each edits, the undo offers and the live frames; a second one on the
    # same folder believed it was alone, so the one-writer-per-folder lock held only inside each,
    # and Stop in one window could not reach a turn the other ran. The OS drops the claim with the
    # process, so a crash never leaves the folder locked (`chimera/core/instance.py`).
    from chimera.core.instance import claim_home, running_url

    claim = claim_home(Path(settings.home))
    if claim is None:
        where = running_url(Path(settings.home))
        console.print(
            "[yellow]Chimera Desktop is already running on this data folder"
            + (f" at {where}" if where else "")
            + ".[/yellow] Use that one, or give this one another CHIMERA_HOME."
        )
        if open_browser and where:
            import webbrowser

            webbrowser.open(where)
        raise typer.Exit(code=3)
    if not settings.can_answer():
        # Unlike run/solve/fuse (which need a model to do their job and stay strict), the desktop app
        # can BOOT keyless: LLMGateway() below is lazy (no model call), and the UI opens a first-run
        # setup screen that adds + live-tests a key from the browser — or, since 0.59.0, picks a
        # model a local runtime already has. So notify and CONTINUE, don't exit.
        console.print(
            "[yellow]No provider key yet[/yellow] — the app opens a setup screen; "
            "add a key there, or pick a local model, to start chatting."
        )

    # Learn what models cost, in the background, once per boot. The receipt under every turn prices
    # itself from a hand-maintained table of ~20 model families, and anything outside it reported
    # "price unknown" — which included the product's own default. The provider publishes the real
    # number for every model it serves; the only thing missing was fetching it before somebody needed
    # it. A daemon thread so a slow index never delays the window opening, and it fails silently: a
    # missing price is exactly the state this improves on, never a reason not to start.
    import threading

    from chimera.providers.listing import warm_price_cache

    threading.Thread(target=warm_price_cache, args=(settings,), daemon=True).start()

    llm = LLMGateway()
    from chimera.fusion import RoutedBackend
    from chimera.fusion.factory import fusion_engine

    # Always available for the per-turn "Fuse this turn" toggle (cheap to construct; runs only on
    # request). Reused as the fusion arm of RoutedBackend when the whole session runs under --fuse.
    fuse_backend = fusion_engine(llm)
    def session_backend() -> SupportsComplete:
        """The backend for ONE conversation, decided when that conversation is built.

        Deciding it once at boot meant the Cascade toggle needed a relaunch to mean anything: the
        screen saved it, re-read it, showed it on — and every later turn still went straight to the
        single model. Built per session instead, it applies to the next conversation. The cron
        daemon keeps the boot-time choice, because it has no conversation to be next to.
        """
        live = get_settings()
        if live.cascade:
            # Honor the Settings "Cascade" toggle: tiered routing (weak -> mid -> fusion).
            return _cascade_backend(llm, live)
        if fuse:
            return RoutedBackend(llm, fuse_backend)
        return llm

    backend: SupportsComplete = session_backend()

    workspace_path = resolve_app_workspace(workspace)
    shared_memory = None if no_memory else _memory_manager()
    shared_graph = _recall_graph(shared_memory)
    shared_profile = shared_memory.profile() if shared_memory is not None else ""

    # Proactivity: fire scheduled jobs while the app is open. The reactive gateway alone never runs
    # the clock, so a "briefing at 7am" would need a separate `chimera serve --cron` terminal. Reuses
    # the exact daemon the serve path uses; the flag overrides the CHIMERA_APP_CRON setting (default
    # on). A keyless boot has no backend to run jobs, so skip until a key exists.
    run_cron = settings.app_cron if cron is None else cron
    cron_stop = (
        _start_cron_daemon(backend, model, max_steps, workspace_path, 30)
        if run_cron and settings.can_answer()
        else None
    )

    # Messaging: let the agent reach the user on Discord/Telegram, started/stopped from the UI. The
    # manager runs each adapter in a background thread (the app already owns the main thread for the
    # HTTP server). Auto-start configured platforms at boot only when CHIMERA_APP_MESSAGING is on.
    from chimera.server import MessagingManager

    messaging = MessagingManager(
        settings=settings,
        backend=backend,
        model=model,
        max_steps=max_steps,
        workspace=workspace_path,
        memory=shared_memory,
        graph=shared_graph,
    )
    if settings.app_messaging:
        for _platform in messaging.platforms():
            if messaging.configured(_platform):
                messaging.start(_platform)
                console.print(f"[dim]messaging: {_platform} adapter started[/dim]")

    # Opt-in MCP autoload: connect the configured MCP servers ONCE at app start and reuse their tools
    # across sessions. Off by default (fast, no subprocess). A broken server is skipped gracefully so
    # it can never break boot; toggling this needs a restart to take effect. Connect eagerly here (not
    # per-session) so the subprocesses aren't respawned for every new chat.
    # Through the shared pool rather than a second set of connections. This block used to build its
    # own, and when the coding surfaces gained MCP they built THEIR own too — so with autoload on,
    # one configured server became two subprocesses, two Docker containers, and for GitHub two
    # sign-ins. Measured on a running app: one connection at boot with no turn run, two after a
    # single turn on the Code screen. `connectors` is idempotent per process, so whichever surface
    # asks first pays for the connect and the other reuses it.
    mcp_connectors = None
    if settings.mcp_autoload:
        from chimera.integrations import mcp_pool

        mcp_connectors = mcp_pool.connectors(settings)
        loaded = len(mcp_connectors.names()) if mcp_connectors is not None else 0
        console.print(f"[dim]MCP autoload: {loaded} server(s) connected[/dim]")

    def _chat_session(*, guarded: bool) -> ChatSession:
        """One assembly, two surfaces, and ``guarded`` is the only thing that differs.

        Written as one function on purpose: the app's chat and ``/v1/chat/completions`` must keep
        the same model, the same identity, the same MCP tools and the same memory, or the product
        answers as two agents from one configuration. What they cannot share is the governance,
        because governance that stops to ask is only governance where somebody can answer — see
        ``guarded``.
        """
        # Read fresh: `settings` above is the boot snapshot, and a conversation built from it would
        # ignore every toggle flipped since launch. These cost nothing to re-read and are decided
        # per conversation anyway, so "next conversation" is the honest scope — a relaunch was never
        # actually required for them.
        live = get_settings()
        registry = default_registry(workspace_path)
        if mcp_connectors is not None:
            # Declared alongside the builtins, or — with `mcp_defer` — three access tools instead of
            # every server's full schema on every step. Read from `live` rather than the boot
            # snapshot: a toggle flipped since launch should reach the next conversation. Through
            # `mount` because the deferred shape has to carry the deployment's lists itself; the
            # fence below cannot see names that are no longer in the registry.
            from chimera.integrations.mcp_defer import mount

            mount(mcp_connectors, registry, live)
        # The owner's OpenAPI connectors (study 29, P7.5), before the lists for the same reason as
        # the MCP tools. No approver yet: on the guarded path the owner's is handed to them below,
        # beside the browser's; unguarded (`/v1/chat/completions`) a non-GET call stays refused.
        # Their answers are fenced only where the ledger below is built — `guarded and
        # live.guard_chat` — so with CHIMERA_GUARD_CHAT off, and always on `/v1/chat/completions`,
        # what a connector returns reaches the model unfenced and does not mark the run.
        from chimera.integrations.openapi_store import ConnectorTool, with_connectors

        with_connectors(registry, live)
        # AFTER the MCP tools, for the same reason the guard below is: a denylist that covers only
        # the tools we wrote is not a denylist. CHIMERA_TOOL_ALLOWLIST/_DENYLIST reached `chimera
        # run` and `chimera solve` and nothing else, so an owner who fenced their agent in `.env`
        # got no fence on this surface — the one `/v1/chat/completions` runs on, and the ONLY
        # control that still reaches that endpoint now that the chat guard does not. (The Discord
        # bot was named here too and never ran on this registry: `MessagingManager` builds its own
        # sessions through `governed_profile`, `chimera/server/manager.py`.)
        # The guard below removes tools by name, so the deferral (which runs inside this call)
        # has to be told what it will remove — otherwise `execute_code` is already behind
        # `tool_call` when the guard looks for it, and stays reachable there.
        from chimera.api.posture import chat_guard_denials

        registry = _apply_tool_allowlist(
            registry,
            allow=None,
            deny=None,
            settings=live,
            later_denials=chat_guard_denials() if guarded and live.guard_chat else None,
        )
        chat_ledger: Any = None
        announcer: Any = None
        if guarded and live.guard_chat:
            # AFTER the MCP tools, so the denylist and the ledger reach those too — a guard that
            # covers only the tools we wrote is not a guard.
            #
            # ON by default since 2026-09-10, and the two things that had to be true first are both
            # in this block. This registry is no longer shared with `/v1/chat/completions` — that
            # endpoint has its own factory below — so arming it here cannot reach an OpenAI client.
            # And the narrowing now has somebody to ask: `_owner_allows` writes the question, the
            # announcer puts it on the turn's own stream, and `POST /api/approvals/{id}` answers it.
            # Measured on the shipped bench, same corpus as every other arm: 7 of 7 attacks blocked
            # either way, over-block 0.750 with nobody to ask and 0.250 with somebody
            # (`bench/right_hand_governance/RESULTS.md` §5b).
            #
            # The messaging gateway was never in this registry's blast radius, whatever this comment
            # used to say: `MessagingManager` builds its own sessions through `governed_profile`.
            from chimera.api.code_api import _owner_allows
            from chimera.api.posture import guard_chat_registry
            from chimera.governance.approval import ApprovalAnnouncer
            from chimera.governance.audit import AuditLog

            # One announcer per CONVERSATION, because that is the lifetime of the registry holding
            # it — `chat_stream` binds its `emit` per TURN and puts it back, which is the same
            # mismatch `on_turn_start` closes for the ledger below.
            announcer = ApprovalAnnouncer()
            approver = _owner_allows(live, announcer)
            # The browser's site list (study 29, P5.2) asks the same person on the same card, as the
            # Code screen does: a page off the list is a question here, not a refusal. Set on the
            # BrowserTool itself, before the ledger wraps it — a wrapper would hold the attribute
            # and the tool would never see it. Only the browser: the file tools' `ask_outside` is a
            # widening of the project folder this surface was never given.
            for tool in registry.tools():
                if getattr(tool, "name", "") == "browser" and getattr(tool, "reach", None) is not None:
                    tool.ask_outside = approver  # type: ignore[attr-defined]  # BrowserTool reads it by getattr
                elif isinstance(tool, ConnectorTool):
                    # A connector's non-GET call asks on the same card (study 29, P7.5).
                    tool.ask_outside = approver
            # The same file the coding turn writes and the Governance screen reads. One log, or the
            # screen shows a partial history while claiming to show the whole one.
            registry, chat_ledger = guard_chat_registry(
                registry,
                audit=AuditLog(live.home / "audit.jsonl"),
                # The deployment's own `CHIMERA_APPROVAL_MODE`, resolved exactly as the coding turn
                # resolves it — one function, so `allow` means the same thing on both surfaces and
                # `ask` reaches the same durable question. Under `ask` with no screen bound, the
                # wait resolves to 0 and the question is refused at once, which is what every
                # non-desktop caller of this session wants.
                approve=approver,
            )
        runner = Agent(
            session_backend(),
            registry,
            AgentConfig(
                model=model,
                max_steps=max_steps,
                # The same identity the coding turn applies. Without it a Discord bot and the app
                # would answer as two different agents from one configuration.
                instructions=render_identity(load_identity(live.home)),
                turn_context=True,
                # …and the same workspace that roots the tools below, so the project's own
                # conventions reach the app's chat the way they reach `solve`.
                project_root=workspace_path,
            ),
        )
        return ChatSession(
            runner,
            memory=shared_memory,
            graph=shared_graph,
            profile=shared_profile,
            remember_from_chat=live.remember_from_chat,
            real_history=live.chat_real_history,
            # The ledger is built once per conversation and the instruction is known once per TURN,
            # which is the whole reason `CHIMERA_TAINT_AUTHORITY` did nothing here: a ledger nobody
            # tells answers `unknown` for every fetch, and the narrowing treats that exactly as it
            # treats `agent`. Measured on the same instrument as the terminal's — 0 rows moved
            # before, 6 after (`bench/right_hand_governance/RESULTS.md`, the `app_chat` columns).
            #
            # `workspace=` so a path the model gives absolutely matches the relative form the person
            # wrote, which is what `set_instruction` documents the argument for.
            on_turn_start=(
                None
                if chat_ledger is None
                else lambda message: chat_ledger.set_instruction(
                    message, workspace=workspace_path
                )
            ),
            # The same ledger learns of a tainted fact the recall hands the prompt (study 30
            # S30-25), so the narrowing arms as it would for a fetched page.
            on_tainted_recall=None if chat_ledger is None else chat_ledger.record_fetch,
            # The other end of the same lifetime problem. The approver above closed over this
            # announcer when the registry was built; `chat_stream` needs to reach it when a turn
            # starts, and the session is the only object both of them hold.
            approval_sink=announcer,
        )

    def factory() -> ChatSession:
        """The app's own chat — the screen a person is sitting in front of, so it is governed."""
        return _chat_session(guarded=True)

    def openai_factory() -> ChatSession:
        """``/v1/chat/completions`` — the assembly this endpoint has always had.

        **Not** governed by ``CHIMERA_GUARD_CHAT``, and that is the point of splitting the factory
        rather than an oversight. The guard's cost is paid in QUESTIONS: on the shipped bench it
        blocks 7 of 7 attacks with an over-block of 0.750 when nobody answers and 0.250 when
        somebody does (`bench/right_hand_governance/RESULTS.md` §5b). Nobody is ever at this
        endpoint — it exists for OpenAI clients and benchmark harnesses — so arming it here buys the
        blocking and pays the full 0.750, on a surface that cannot say why it refused. That is the
        exposure the off-by-default was really protecting, and it is why the default could not be
        flipped until the two surfaces could be told apart.
        `chimera/api/app.py:build_api_app` takes this as ``openai_factory=``.

        Fencing this endpoint is still available and is a different control: ``CHIMERA_TOOL_DENYLIST``
        is applied by ``_apply_tool_allowlist`` above, on this path, and it refuses without asking
        anyone — which is the right shape for a surface with no person in it.
        """
        return _chat_session(guarded=False)

    # The built SPA, if present, is served same-origin (no CORS). Absent = API-only (dev uses Vite).
    # Prefer a source-checkout build (live `npm run build` output); fall back to the copy bundled in the
    # wheel (chimera/_desktop_dist) so a pip-installed `[desktop]` user gets the UI, not just the API.
    # One level deeper than when this lived in chimera/cli/main.py, hence parents[3] / parents[2].
    _dev_dist = Path(__file__).resolve().parents[3] / "apps" / "desktop" / "dist"
    _pkg_dist = Path(__file__).resolve().parents[2] / "_desktop_dist"
    dist = _dev_dist if (_dev_dist / "index.html").exists() else _pkg_dist
    static_dir = dist if (dist / "index.html").exists() else None
    api = build_api_app(
        factory,
        # The one surface that must NOT inherit the app chat's governance — see `openai_factory`.
        openai_factory=openai_factory,
        # NOT `settings=settings`. Passing one means "use THIS, frozen" — which is what a test or a
        # bench comparing two configurations asks for, and the opposite of what the app needs. The
        # object here IS `get_settings()`, so passing it changed nothing except to freeze
        # `live_settings()` at launch: every row the Settings screen writes through
        # `PATCH /api/config` — the reach floor, the deployment denylist, the vision model — kept
        # returning the boot value while the screen said "saved". `live_settings()`'s own docstring
        # states the rule this broke: "Saving a setting that silently does nothing is worse than not
        # offering it, because the confirmation is what turns it into a lie."
        #
        # `home` stays fixed regardless: `build_api_app` binds it once from `get_settings()` and the
        # data directory must not move under an open session.
        static_dir=static_dir,
        fuse_backend=fuse_backend,
        workspace=workspace_path,
        messaging_manager=messaging,
        # The same memory the chat reads, now reachable from a coding turn too — read-only.
        memory=shared_memory,
        graph=shared_graph,
    )

    # Bind BEFORE announcing so the URL reflects the real port (a busy 8765 falls back to a free one
    # instead of crashing). The bound socket is handed to uvicorn, so there is no close-then-rebind gap.
    sock, port = _bind_app_socket(host, port)
    # The access card reports where this listener is: with `--host 0.0.0.0` the guest app mounted at
    # /guest answers the network with any share link, and the card has to say so rather than
    # describe only the separate LAN door.
    api.state.bound_address = (host, port)
    url = f"http://{host}:{port}"
    if emit_port_file:  # discovery channel for a parent process (the Tauri sidecar reads this)
        Path(emit_port_file).write_text(url, encoding="utf-8")
    claim.announce(url)
    # The desktop bridge learns its port only now. With "Allow Claude to operate this app" on, this
    # writes the discovery file `chimera mcp desktop` reads; off, it only clears a stale one. A
    # wildcard bind is reached on loopback — the bridge is for a client on THIS machine.
    bridge_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    desktop_bridge = api.state.desktop_bridge
    try:
        desktop_bridge.attach(f"http://{bridge_host}:{port}")
    except OSError as exc:  # a bridge that cannot write its file must not keep the app from starting
        console.print(f"[yellow]desktop bridge not started: {exc}[/yellow]")
    ui_note = "" if static_dir is not None else "  [yellow](UI not built — API only; run 'npm --prefix apps/desktop run build')[/yellow]"
    console.print(f"[bold]Chimera Desktop[/bold] on {url}  [dim](API at /api). Ctrl+C to stop.[/dim]{ui_note}")
    if open_browser and static_dir is not None:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    # Server.run(sockets=[...]) uses our already-bound socket (uvicorn.run rebinds host/port itself).
    try:
        uvicorn.Server(uvicorn.Config(api, log_level="warning")).run(sockets=[sock])
    finally:
        # First, so the token dies with the server even if a later step raises.
        desktop_bridge.close()
        claim.release()
        if cron_stop is not None:
            cron_stop.set()  # stop the cron daemon thread on Ctrl+C / shutdown
        messaging.stop_all()  # close any running messaging adapters


def _start_cron_daemon(
    backend: SupportsComplete, model: str | None, max_steps: int, workspace: Path, tick: int
) -> Any:
    """Start the cron daemon in a background thread; return its stop event."""

    from chimera.core import Agent, AgentConfig
    from chimera.scheduler import CronDaemon, Scheduler, make_agent_dispatch
    from chimera.scheduler.delivery import make_deliver, make_failure_notifier
    from chimera.scheduler.job_runner import make_run_job
    from chimera.tools import default_registry

    scheduler = Scheduler(_cron_store())
    settings = get_settings()
    usage_path = settings.home / "usage.jsonl"

    # Built by the factory rather than defined here. As a closure inside this command it was
    # unreachable from any test, and that is why two defects shipped: the verdict it returns
    # and the workspace it names are both produced HERE, and nothing could drive it to check.
    run_job = make_run_job(
        settings=settings,
        backend=backend,
        workspace=workspace,
        model=model,
        max_steps=max_steps,
        usage_path=usage_path,
        warn=lambda linha: console.print(f"[yellow]{linha}[/yellow]"),
        # Per dispatch, not off `settings` above: this daemon lives as long as the app, and the cap
        # is set from the Usage screen. Read off the snapshot, a cap saved there braked nothing
        # until the next launch while the screen said it was set.
        daily_cap=lambda: get_settings().daily_usd_cap,
    )

    def run_task(task: str) -> str:
        """The job-less fallback. Governed too — it is reachable, and "reachable but forgotten" is
        the exact shape of the hole this whole change closes."""
        registry, _ = governed_profile(
            default_registry(workspace),
            settings=settings,
            home=settings.home,
            surface="cron:task",
            instruction=task,
            workspace=workspace,
        )
        agent = Agent(
            backend, registry,
            AgentConfig(
                model=model, max_steps=max_steps, project_root=workspace,
                instructions=owner_identity(get_settings().home),
                turn_context=True,
            ),
        )
        return agent.run(task).answer

    results_path = get_settings().home / "scheduler" / "cron_results.jsonl"

    # The result file always, and the job's chat webhook when it names one. Built by
    # `make_deliver` rather than written here: the defect it replaces was in the wiring rather than
    # in any mechanism — `deliver_to` was declared, copied into the record, and read by nothing —
    # and a sink that lives in a module is one a test can reach.
    deliver = make_deliver(
        results_path, warn=lambda linha: console.print(f"[yellow]{linha}[/yellow]")
    )

    # A run that could not run or finish is announced at the job's webhook, once per outage and
    # per failure kind in it. The flag is asked per tick (`get_settings()`, which `PATCH
    # /api/config` refreshes), so switching it off silences the next tick rather than the next launch.
    notices = make_failure_notifier(
        warn=lambda linha: console.print(f"[yellow]{linha}[/yellow]"),
        enabled=lambda: get_settings().cron_notify_failures,
    )

    # A job that is running holds the machine whatever fired it (`holding`), and a clock job due
    # within the next minutes holds it too (the probe below). Both here, beside the daemon that
    # fires them, because a schedule nobody in this process will fire is no reason to stay up. Both
    # do nothing unless CHIMERA_KEEP_AWAKE is on (`chimera/core/keep_awake.py`).
    from chimera.core.keep_awake import cron_due_probe, holding
    from chimera.core.keep_awake import service as keep_awake_service

    daemon = CronDaemon(
        scheduler,
        holding("cron", make_agent_dispatch(run_task, deliver, run_job=run_job)),
        tick_seconds=tick,
        on_outcome=notices,
    )
    _thread, stop = daemon.start()
    keep_awake_service().add_probe("cron", cron_due_probe(scheduler))
    jobs = len(scheduler.store.list())
    console.print(f"[dim]cron daemon on (tick {tick}s, {jobs} job(s) scheduled)[/dim]")
    return stop


def _messaging_adapter(
    settings: Settings, platform: str, *, home_was_empty: bool | None = None
) -> Any:
    """Build the requested platform adapter (Discord/Telegram/Slack/Signal) or exit with guidance.

    Each one gets the owner's allowlist for its platform. The adapters have accepted one since they
    shipped and this function never passed it, so `chimera serve --discord` answered anyone who
    could reach the bot. An empty list still means "anyone" (the owner's decision — see
    `chimera/server/allowlist.py`), but no longer silently.
    """
    from chimera.server.allowlist import PairingFlow, is_new_install, open_bot_warning

    adapter = _build_messaging_adapter(settings, platform)
    if adapter.allowed_users is None:
        if is_new_install(settings, platform, home_was_empty=home_was_empty):
            adapter.pairing_flow = PairingFlow(platform, settings.home)
            console.print(
                f"[bold yellow]Pairing code for {platform}: {adapter.pairing_flow.code} "
                "(expires in 10 minutes; first DM claims it)[/bold yellow]"
            )
        else:
            console.print(f"[bold yellow]NOTICE:[/bold yellow] [yellow]{open_bot_warning(platform)}[/yellow]")
    from chimera.server.attachments import attach_refusal

    refusal = attach_refusal(settings, platform)
    if refusal:
        console.print(f"[bold red]WARNING:[/bold red] [yellow]{refusal}[/yellow]")
    return adapter


def _warn_open_bot(platform: str) -> None:
    from chimera.server.allowlist import open_bot_warning

    console.print(f"[bold red]WARNING:[/bold red] [yellow]{open_bot_warning(platform)}[/yellow]")


def _build_messaging_adapter(settings: Settings, platform: str) -> Any:
    from chimera.server.allowlist import allowed_users_for

    allowed = allowed_users_for(settings, platform)
    if platform == "discord":
        if not settings.discord_bot_token:
            console.print("[red]Set CHIMERA_DISCORD_BOT_TOKEN to run the Discord adapter.[/red]")
            raise typer.Exit(code=1)
        from chimera.server import DiscordAdapter

        # Attachments are armed by `_serve_platform`, which knows the workspace they are checked in.
        return DiscordAdapter(
            settings.discord_bot_token, allowed_users=allowed,
            inbound_media=settings.chat_inbound_media,
        )
    if platform == "telegram":
        if not settings.telegram_bot_token:
            console.print("[red]Set CHIMERA_TELEGRAM_BOT_TOKEN to run the Telegram adapter.[/red]")
            raise typer.Exit(code=1)
        from chimera.server import TelegramAdapter

        return TelegramAdapter(
            settings.telegram_bot_token, allowed_users=allowed,
            inbound_media=settings.chat_inbound_media,
        )
    if platform == "slack":
        if not (settings.slack_bot_token and settings.slack_app_token):
            console.print("[red]Set CHIMERA_SLACK_BOT_TOKEN and CHIMERA_SLACK_APP_TOKEN to run the Slack adapter.[/red]")
            raise typer.Exit(code=1)
        from chimera.server import SlackAdapter

        return SlackAdapter(settings.slack_bot_token, settings.slack_app_token, allowed_users=allowed)
    if not (settings.signal_api_url and settings.signal_number):
        console.print("[red]Set CHIMERA_SIGNAL_API_URL and CHIMERA_SIGNAL_NUMBER (run a signal-cli-rest-api bridge).[/red]")
        raise typer.Exit(code=1)
    from chimera.server import SignalAdapter

    return SignalAdapter(settings.signal_api_url, settings.signal_number, allowed_users=allowed)


def _sender_registry(settings: Settings, primary: Any = None) -> Any:
    """A SenderRegistry with the primary adapter (if any) plus configured push senders (WhatsApp)."""
    from chimera.integrations import SenderRegistry

    registry = SenderRegistry()
    if primary is not None:
        registry.register(primary)
    if settings.whatsapp_access_token and settings.whatsapp_phone_number_id:
        from chimera.server import WhatsAppSender

        registry.register(WhatsAppSender(settings.whatsapp_access_token, settings.whatsapp_phone_number_id))
    return registry


@app.command("acp")
def acp_server(
    workspace: str = typer.Option(".", "--workspace", "-w", help="Directory the agent works in."),
    model: str | None = typer.Option(None, "--model", help="Override the model for this session."),
    max_steps: int = typer.Option(30, "--max-steps", help="Tool-calling steps per turn."),
) -> None:
    """Serve Chimera to an editor over the Agent Client Protocol (stdio).

    The mirror of what a Code-screen turn with `provider: claude` does: there we drive somebody
    else's agent, here somebody else's editor drives ours. Point Zed, JetBrains or Neovim at
    `chimera acp` and the loop, the verifier and the receipt are available without installing a
    second tool.

    Nothing on this path may write to stdout — it IS the protocol. A stray print corrupts the frame
    the editor is parsing, and the symptom is an editor that hangs rather than output in the wrong
    place. The banner goes to stderr for the same reason the MCP server's does.
    """
    import sys

    from chimera.acp.server import AcpServer
    from chimera.core import Agent, AgentConfig
    from chimera.governance import governed_profile
    from chimera.providers import LLMGateway
    from chimera.tools import default_registry

    settings = get_settings()
    if not settings.can_answer():
        # stderr, not the console: stdout is the wire, and an editor parsing a Rich panel as a
        # JSON-RPC frame reports a hang rather than a missing key.
        print("No provider key configured, and the default model is not a local one. Run 'chimera doctor'.", file=sys.stderr)
        raise typer.Exit(code=1)
    workspace_path = Path(workspace).resolve()
    backend = LLMGateway()
    registry, approvals = governed_profile(
        default_registry(workspace_path),
        settings=settings,
        home=settings.home,
        surface="acp",
    )
    agent = Agent(
        backend,
        registry,
        AgentConfig(
            model=model or settings.default_model,
            max_steps=max_steps,
            project_root=workspace_path,
            trace_path=settings.home / "traces.jsonl",
            instructions=owner_identity(settings.home),
            turn_context=True,
        ),
    )

    def run_turn(prompt: str, on_token: Any) -> str:
        result = agent.run(prompt, on_token=on_token)
        if approvals.blocked:
            # Surfaced in the answer, because an editor shows the answer and nothing else. A
            # governance refusal that lived only in a log would leave a person reading a confident
            # reply about work that was not allowed to happen.
            return f"{result.answer}\n\n[governance] {approvals.summary()}"
        return result.answer

    print(
        f"Chimera ACP agent on stdio (workspace {workspace_path}). Ctrl+C to stop.",
        file=sys.stderr,
    )
    AcpServer(run_turn, workspace=str(workspace_path)).serve_forever()


def _serve_mcp(
    backend: SupportsComplete,
    gateway: Any,  # LLMGateway (for the fusion engine)
    model: str | None,
    max_steps: int,
    workspace_path: Path,
    *,
    recall: bool,
) -> None:
    """Expose Chimera as an MCP server on stdio: solve/fuse/memory-search become MCP tools.

    stdio is the MCP wire, so nothing here may write to stdout — the notice goes to stderr.
    """
    import sys

    from chimera.core import Agent, AgentConfig, AutonomousAgent, AutonomousConfig
    from chimera.fusion.factory import fusion_engine
    from chimera.providers import Message
    from chimera.server import ChimeraMCP
    from chimera.tools import default_registry

    def _solve(task: str) -> str:
        registry, _ = governed_profile(
            default_registry(workspace_path),
            settings=get_settings(),
            home=get_settings().home,
            surface="mcp",
            instruction=task,
            workspace=workspace_path,
        )
        worker = Agent(
            backend, registry,
            AgentConfig(
                model=model, max_steps=max_steps, project_root=workspace_path,
                instructions=owner_identity(get_settings().home),
                turn_context=True,
            ),
        )
        auto = AutonomousAgent(
            worker,
            memory=_memory_manager() if recall else None,
            config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=False),
        )
        return auto.run(task).answer or "(no answer)"

    def _fuse(prompt: str) -> str:
        return fusion_engine(gateway).run([Message(role="user", content=prompt)]).final

    def _search(query: str, k: int) -> list[str]:
        return [item.content for item in _memory_manager().search(query, k=k)]

    from chimera.tools.decide import make_decide

    # `chimera_decide` is listed to MCP clients always: the schema cost is the client's, paid only by
    # a client that lists tools, and the decider is built on the first call, not here.
    bridge = ChimeraMCP(solve=_solve, fuse=_fuse, memory_search=_search, decide=make_decide())
    print(
        "chimera MCP server on stdio — tools: chimera_solve, chimera_fuse, chimera_memory_search, chimera_decide",
        file=sys.stderr,
    )
    try:
        bridge.serve_stdio()
    except ModuleNotFoundError as exc:
        print(f"MCP SDK missing — install with: pip install 'chimera-agent[mcp]' ({exc})", file=sys.stderr)
        raise typer.Exit(code=1) from exc
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)


def _build_a2a(
    backend: SupportsComplete,
    model: str | None,
    max_steps: int,
    workspace_path: Path,
    host: str,
    port: int,
) -> tuple[Any, dict[str, Any]]:
    """Build the (A2AServer, agent_card) pair whose ``solve`` runs the autonomous agent."""
    from chimera.core import Agent, AgentConfig, AutonomousAgent, AutonomousConfig
    from chimera.integrations import A2AServer, chimera_agent_card
    from chimera.tools import default_registry

    def _solve(task: str) -> str:
        registry, _ = governed_profile(
            default_registry(workspace_path),
            settings=get_settings(),
            home=get_settings().home,
            surface="a2a",
            instruction=task,
            workspace=workspace_path,
        )
        worker = Agent(
            backend, registry,
            AgentConfig(
                model=model, max_steps=max_steps, project_root=workspace_path,
                instructions=owner_identity(get_settings().home),
                turn_context=True,
            ),
        )
        auto = AutonomousAgent(
            worker, config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=False)
        )
        return auto.run(task).answer or "(no answer)"

    url = f"http://{host}:{port}/a2a"
    return A2AServer(_solve), chimera_agent_card(url, version=__version__)


@app.command("a2a-card")
def a2a_card(
    url: str = typer.Option("http://127.0.0.1:8765/a2a", "--url", help="The A2A endpoint URL to advertise."),
) -> None:
    """Print Chimera's A2A Agent Card JSON (serve it at /.well-known/agent.json)."""
    import json as _json

    from chimera.integrations import chimera_agent_card

    console.print_json(_json.dumps(chimera_agent_card(url, version=__version__)))


def _serve_platform(
    adapter: Any,  # an Adapter that is also a MessageSender (Discord/Telegram/Slack)
    settings: Settings,
    backend: SupportsComplete,
    model: str | None,
    max_steps: int,
    workspace_path: Path,
    memory: MemoryManager | None,
    graph: MemoryGraph | None,
) -> None:
    """Serve the gateway over a platform adapter: one session per chat; the agent can send."""
    from chimera.core import Agent, AgentConfig
    from chimera.core.agent import attended
    from chimera.core.jobs import finished_note
    from chimera.integrations import SendMessageTool
    from chimera.interface import ChatSession
    from chimera.server import MessageGateway
    from chimera.tools import default_registry

    senders = _sender_registry(settings, adapter)  # this platform + any configured push senders
    send_tool = SendMessageTool(senders)

    def factory() -> ChatSession:
        # See `serve`, which has the same closure and the same reason: one registry per chat, one
        # instruction per turn, so the ledger is told through `ChatSession.on_turn_start` and not
        # through `instruction=`. Both are wired in one change rather than one of them, because this
        # exact defect has now been found on five surfaces and repaired on four separate occasions
        # (#400 chat/assist, #405 tui, #408 the app, this) — each time by somebody looking at one
        # surface and not asking the same question of its neighbour.
        turn_ledger: Any = None

        def _hold(ledger: Any) -> None:
            nonlocal turn_ledger
            turn_ledger = ledger

        registry, _ = governed_profile(

            default_registry(workspace_path),
            settings=get_settings(),
            home=get_settings().home,
            surface=f"platform:{adapter.platform}",
            on_ledger=_hold,
            # The bot's voice: exempt from the owner's fence (a denylist aimed at the shell must not
            # silence the bot) but inside the kernel and the taint ledger, which registering it
            # after this call skipped — so a turn that had read an attacker's page could still send
            # anything to any chat id.
            voice=[send_tool],
        )
        if os.environ.get("CHIMERA_CHAT_SCHEDULE_ONCE", "").strip().lower() in {"1", "true", "yes", "on"}:
            from chimera.governance.approval import always_ask
            from chimera.tools.schedule_once import ScheduleOnceTool

            registry.register(ScheduleOnceTool(
                home=get_settings().home,
                workspace=workspace_path,
                approve=always_ask(get_settings().home),
            ))
        runner = Agent(
            backend, registry,
            # A person is waiting on the other end of the chat, as at the terminal: see `attended`.
            attended(AgentConfig(
                model=model, max_steps=max_steps, project_root=workspace_path,
                # The same identity the app's own bot and the coding turn apply. Without it the
                # bot `serve --discord` starts answered as a different agent from the one the
                # owner configured, in whatever language the message happened to be in.
                instructions=owner_identity(get_settings().home),
                turn_context=True,
            )),
        )
        live = get_settings()
        return ChatSession(
            runner,
            memory=memory,
            graph=graph,
            # The three things the terminal gives its session and this one did not: who the owner
            # is (the stored profile and memory's persona), recalled facts quoted with their source
            # and date under the "possibly stale" header, and the owner's own "remember that…"
            # switch. The production bot was the one surface answering the owner without them.
            # `remember_from_chat` is passed through as set, never turned on here; and there is
            # still no `extractor`: anyone who can reach a bot would be writing the owner's memory.
            profile=_session_profile(memory),
            cite_facts=live.memory_extract,
            remember_from_chat=live.remember_from_chat,
            # The path `serve --discord` runs, which is the production bot.
            real_history=live.chat_real_history,
            # A shell command that outlived its timeout kept running as a job; this is how the
            # chat hears that it ended.
            turn_note=lambda: finished_note(get_settings().home, workspace_path),
            # `None` under the shipped `CHIMERA_GOVERNANCE=off`, where no ledger is built at all.
            on_turn_start=(
                None
                if turn_ledger is None
                else lambda message: turn_ledger.set_instruction(
                    message, workspace=workspace_path
                )
            ),
            # The same ledger learns of a tainted fact the recall hands the prompt (study 30
            # S30-25), so the narrowing arms as it would for a fetched page.
            on_tainted_recall=None if turn_ledger is None else turn_ledger.record_fetch,
            # Inbound voice/images (S30-46): the transcript is untrusted, so it enters the run's
            # ledger as a fetch nobody named — `unknown` arms the narrowing under both modes.
            on_tainted_input=(
                None
                if turn_ledger is None
                else lambda content: turn_ledger.record_fetch(
                    "inbound-media", content, requested_by="unknown"
                )
            ),
        )

    from chimera.server.allowlist import is_listed_owner

    gateway = MessageGateway(
        factory, warnings_in_reply=True, name_the_channel=True,
        intercept=_chat_approvals(settings, adapter.platform),
        # Who the owner is, so a "remember that..." from anyone else is written tainted and
        # names its sender (study 30 S30-29). Read live, as the allowlist is.
        owner_of=lambda message: is_listed_owner(get_settings(), message),
        attach=_turn_attachments(adapter, settings, workspace_path),
    )
    console.print(
        f"[bold]Chimera on {adapter.platform}[/bold] "
        "[dim]— message the bot; each chat is its own session. Ctrl+C to stop.[/dim]"
    )
    try:
        adapter.start(gateway.on_message)  # blocking until interrupted
    except KeyboardInterrupt:
        console.print("\n[dim]stopped[/dim]")
    except ImportError:
        console.print("[red]This platform needs an extra dependency: uv sync --extra messaging[/red]")
        raise typer.Exit(code=1) from None
    finally:
        adapter.stop()


def _turn_attachments(adapter: Any, settings: Settings, workspace: Path) -> Any:
    """Arm the adapter's attachments when the owner asked and it is safe, and return the gateway's
    ``attach`` hook — or ``None`` (nothing is collected) for an adapter that does not attach.

    The adapter decides last: ``enable_attachments`` refuses for a bot with no allowlist whatever this
    passes, and the hook follows what the adapter ended up with."""
    from chimera.server.attachments import attach_enabled

    enable = getattr(adapter, "enable_attachments", None)
    if enable is not None and attach_enabled(settings, str(getattr(adapter, "platform", ""))):
        enable(workspace)
        console.print("[dim]attachments: on — files this bot's turns write go with the reply[/dim]")
    if not getattr(adapter, "attach_files", False):
        return None
    from functools import partial

    from chimera.server.attachments import turn_attachments

    return partial(turn_attachments, workspace=workspace)


def _chat_approvals(settings: Settings, platform: str) -> Any:
    """The bot's interceptor for approvals typed into the chat, warning when it cannot apply.

    Installed whether or not `CHIMERA_APPROVE_VIA_CHAT` is on: with it off, a message shaped like
    an answer is still refused and still kept out of the session, so a code pasted into the chat
    never reaches the model. See `chimera/server/chat_approval.py`.
    """
    from chimera.server.chat_approval import ChatApprovals, enabled_platforms, startup_warning

    warning = startup_warning(settings, platform)
    if warning:
        console.print(f"[bold red]WARNING:[/bold red] [yellow]{warning}[/yellow]")
    elif platform in enabled_platforms(settings):
        console.print(
            f"[dim]approvals from {platform}: on — `aprovar <id> <code>` from a listed id[/dim]"
        )
    return ChatApprovals(settings, settings.home).intercept


def _whatsapp_webhook(
    settings: Settings, gateway: MessageGateway, *, home_was_empty: bool | None = None
) -> Any:
    """A WhatsAppWebhook (Meta verification + inbound routing) when configured, else None."""
    if not (
        settings.whatsapp_access_token
        and settings.whatsapp_phone_number_id
        and settings.whatsapp_verify_token
    ):
        return None
    from chimera.server import WhatsAppSender, WhatsAppWebhook

    # New installs must authenticate Meta's webhook signature. Existing setups are never blocked.
    from chimera.server.allowlist import (
        WHATSAPP_UNSIGNED_WARNING,
        allowed_users_for,
        is_new_install,
    )

    allowed = allowed_users_for(settings, "whatsapp")
    new_install = is_new_install(settings, "whatsapp", home_was_empty=home_was_empty)
    if new_install and not settings.whatsapp_app_secret:
        console.print("[red]Set CHIMERA_WHATSAPP_APP_SECRET before configuring a new WhatsApp webhook.[/red]")
        return None
    pairing_flow = None
    if allowed is None:
        if new_install:
            from chimera.server.allowlist import PairingFlow

            pairing_flow = PairingFlow("whatsapp", settings.home)
            console.print(
                f"[bold yellow]Pairing code for whatsapp: {pairing_flow.code} "
                "(expires in 10 minutes; first DM claims it)[/bold yellow]"
            )
        else:
            _warn_open_bot("whatsapp")
    if not settings.whatsapp_app_secret and not new_install:
        console.print(f"[bold yellow]NOTICE:[/bold yellow] [yellow]{WHATSAPP_UNSIGNED_WARNING}[/yellow]")
    sender = WhatsAppSender(settings.whatsapp_access_token, settings.whatsapp_phone_number_id)
    return WhatsAppWebhook(
        sender, settings.whatsapp_verify_token, gateway.on_message,
        app_secret=settings.whatsapp_app_secret,
        allowed_numbers=allowed,
        inbound_media=settings.chat_inbound_media,
        pairing_flow=pairing_flow,
    )


def _webhook_handler(gateway: MessageGateway) -> Any:
    """Fire scheduler webhook jobs (added via `cron add --webhook`) through the gateway."""
    import json as _json
    import time

    from chimera.governance.ledger_tool import fence
    from chimera.scheduler import Scheduler
    from chimera.scheduler.weekly_review import builtin_of
    from chimera.server import InboundMessage

    scheduler = Scheduler(_cron_store())

    def webhooks(hook: str, payload: dict[str, Any]) -> list[str]:
        results: list[str] = []

        def dispatch(job: Any) -> None:
            if job.tools is not None or job.notify != "always":
                # Fail closed. `cron add --webhook` refuses these fields, so a webhook job carrying
                # them was edited into jobs.json by hand — and the gateway below would run it with
                # every tool and post every answer while `cron list` printed the fence. Not run is
                # the honest outcome; fire_webhook logs the reason.
                raise ValueError(
                    f"webhook job {job.name!r} sets tools/notify, which the webhook path does not "
                    "apply; refusing to run it with every tool"
                )
            if builtin_of(job):
                # A builtin (the weekly review) is computed by code and only `make_run_job` knows
                # how. Here its action text would reach the gateway's model as a prompt — the one
                # thing a builtin exists not to do. Only a hand edit of jobs.json gets here.
                raise ValueError(
                    f"webhook job {job.name!r} names a builtin, which only the cron daemon runs"
                )
            prompt = job.action
            if payload:
                # Fenced: the payload is whatever the sender POSTed, and the job's action is the
                # only part the owner wrote. Unfenced, a pull-request title reading "ignore the
                # task above" arrived as more instructions (sleeper-channels audit, channel 4).
                prompt = f"{prompt}\n\nWebhook payload:\n{fence(_json.dumps(payload))}"
            results.append(
                gateway.on_message(
                    InboundMessage(text=prompt, chat_id=f"webhook:{hook}", platform="webhook")
                )
            )

        # Pick up webhook jobs added via `cron add --webhook` after the server started — the cron
        # daemon reloads each tick, but this handler holds a frozen store, so without this a newly
        # registered hook would silently do nothing until a restart.
        scheduler.store.reload_if_changed()
        # Held while it runs, like a clock job: the hint says "a scheduled task", and a webhook job
        # is one (`chimera/core/keep_awake.py`; nothing unless CHIMERA_KEEP_AWAKE is on).
        from chimera.core.keep_awake import holding

        scheduler.fire_webhook(hook, time.time(), holding("cron", dispatch))
        return results

    return webhooks
