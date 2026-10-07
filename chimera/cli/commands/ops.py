"""Operations: approve, secrets, cron and report.

Moved verbatim out of ``chimera/cli/main.py`` (S30-70); ``chimera.cli.main`` re-exports every
name defined here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.markup import escape
from rich.table import Table

from chimera.cli.commands._shared import app, console
from chimera.config import get_settings

if TYPE_CHECKING:
    from chimera.scheduler import CronStore



# --- cron subcommands ---------------------------------------------------------

@app.command()
def approve(
    request_id: str = typer.Argument(None, help="The id from the message. Omit to list what is waiting."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve it."),
    no: bool = typer.Option(False, "--no", "-n", help="Refuse it."),
    show: bool = typer.Option(
        False, "--show", help="Print the whole question — the full action — and answer nothing."
    ),
    code: str = typer.Option(
        "", "--code", help="The question's code, from the message that asked. Needed to approve."
    ),
) -> None:
    """Answer a decision the agent is waiting on, from anywhere.

    Without a terminal the approval gate collapsed to a refusal: `ask` degraded to `deny`, so every
    REVIEW verdict on the VPS, in a container or under cron was a no, and the mandate that says
    "confirm before billing, before a destructive migration, before touching RLS" had nothing to
    confirm with. The question is written down and sent to wherever this deployment delivers; this
    is how it gets answered.

    Silence is still a refusal — a question times out. That is deliberate: a gate that reads silence
    as consent produces a record of an approval nobody gave.
    """
    from chimera.governance.pending import answer as responder
    from chimera.governance.pending import answer_stats, code_shown
    from chimera.governance.pending import pending as esperando
    from chimera.interface import render

    home = Path(get_settings().home)
    if not request_id:
        aguardando = esperando(home)
        if not aguardando:
            console.print("[dim]nothing is waiting for a decision[/dim]")
        else:
            tabela = Table(title="Waiting for you")
            tabela.add_column("id")
            tabela.add_column("level")
            tabela.add_column("waiting")
            tabela.add_column("why")
            tabela.add_column("action")
            for p in aguardando:
                # Forty characters of `why` used to be the whole question. The reason now names the
                # page and who asked for it; cutting it back to the tool name would undo that.
                tabela.add_row(
                    p.id, p.decision, f"{p.age_seconds / 60:.0f} min", p.reason[:160],
                    p.action[:120] + ("…" if len(p.action) > 120 else ""),
                )
            console.print(tabela)
            # The table cuts the action; the yes is about all of it. A pull request's card is the
            # text that will be published, and answering from this table alone would approve the
            # first 120 characters of it.
            console.print("[dim]read the whole question: chimera approve <id> --show[/dim]")
            console.print("[dim]answer with: chimera approve <id> --yes --code <code> | --no[/dim]")
            # Where the code is. Not here: anything this command can print from the queue, the
            # agent's shell could read from it too, and the code is what tells the two apart.
            console.print(
                "[dim]the code is in the message that asked (or the output of the process that "
                "asked); refusing needs none[/dim]"
            )
            # Except where it is not: a question the app asked with no channel has its code only in
            # the app's memory, and one asked with no terminal and no channel has it nowhere. Said
            # per id, so nobody goes looking for a message that was never sent.
            for p in aguardando:
                why_not = _NOT_APPROVABLE_HERE.get(code_shown(home, p.id))
                if why_not:
                    console.print(f"[dim]{p.id}: {why_not}[/dim]")
        # The operating metrics of this mechanism, because a gate whose questions nobody answers
        # behaves exactly like no gate while its block rate still reads perfect. Printed here, on
        # the command a person runs to answer, so the person answering is the one who sees whether
        # anyone does.
        console.print(render.approval_stats_line(answer_stats(home)))
        return

    if show:
        if yes or no:
            # Reading and answering in one command would be answering before reading.
            console.print("[yellow]--show answers nothing; read it, then run --yes or --no[/yellow]")
            raise typer.Exit(code=1)
        found = next((p for p in esperando(home) if p.id == request_id), None)
        if found is None:
            console.print(f"[yellow]no question waiting with id {request_id}[/yellow]")
            raise typer.Exit(code=1)
        # Plain print, not the console: the action holds text the agent wrote, and rich would read
        # its square brackets as markup and drop them — the one place the full text must be exact.
        # Control characters written out (`approval.visible`), so the text cannot drive the terminal.
        from chimera.governance.approval import visible

        print(visible(f"{found.id} ({found.decision}): {found.reason}\n\n{found.action}"))
        return

    if yes == no:
        # Both or neither. An approval this important must be typed, never inferred from a default:
        # whichever way the default fell, half the answers would be the one nobody chose.
        console.print("[yellow]say which: --yes or --no[/yellow]")
        raise typer.Exit(code=1)
    from chimera.governance import setting_suggestions

    if setting_suggestions.is_suggestion(home, request_id):
        if yes:
            # Approved only in the app. Not because a terminal is less the owner's than a screen:
            # the change is applied by the app's own save, which also updates the RUNNING app, and a
            # `.env` written from here could be another folder's and would reach the app only at its
            # next launch. And a shell the agent was given could run this line.
            console.print(
                f"[yellow]{request_id} is a settings change somebody suggested; approve it in the "
                "app (the card shows the value now and the value proposed). From here it can only "
                "be refused: chimera approve <id> --no[/yellow]"
            )
            raise typer.Exit(code=1)

        def _never(_updates: dict[str, str]) -> None:  # a refusal applies nothing
            raise ValueError("a refusal applies nothing")

        outcome, _ = setting_suggestions.resolve(
            home,
            request_id,
            False,
            via="cli",
            current_of=lambda _key: "",
            check=_never,
            apply=_never,
            allowed=lambda _key: False,
        )
        if outcome != "refused":
            console.print(f"[yellow]no question waiting with id {request_id}[/yellow]")
            raise typer.Exit(code=1)
        console.print(f"[green]refused[/green] {request_id}")
        return
    why_not = _NOT_APPROVABLE_HERE.get(code_shown(home, request_id)) if yes else None
    if why_not:
        # Said before asking for a code that does not exist anywhere the person can read it.
        console.print(f"[yellow]{request_id}: {why_not}[/yellow]")
        raise typer.Exit(code=1)
    if yes and not code.strip():
        # Asked BEFORE anything is written, and never filled from this process's memory: in real
        # use this command is a separate process and has none, and a test that ran it in-process
        # must not see a different command (study 30, S30-30). An answer file that approves is
        # honoured only with the code the owner was sent, so a shell that can write files cannot
        # approve; this line is how the owner hands it over.
        console.print(
            "[yellow]approving needs the question's code: chimera approve "
            f"{request_id} --yes --code <code>. It is in the message that asked (or in the output "
            "of the process that asked). Refusing needs none: --no[/yellow]"
        )
        raise typer.Exit(code=1)
    if not responder(home, request_id, yes, via="cli", code=code.strip() if yes else None):
        console.print(f"[yellow]no question waiting with id {request_id}[/yellow]")
        raise typer.Exit(code=1)
    if yes:
        # The asker checks the code; a wrong one refuses the question, once, and it is recorded.
        console.print(
            f"[green]answered[/green] {request_id}: approved if the code is right "
            "(a wrong code refuses the question)"
        )
        return
    console.print(f"[green]refused[/green] {request_id}")


#: Why ``chimera approve <id> --yes`` cannot work, by where the question's code was shown
#: (`pending.code_shown`). Study 30, S30-30: before this the command sent a person looking for the
#: code in "the message that asked" for a question that had no message and printed no code.
_NOT_APPROVABLE_HERE = {
    "screen": (
        "asked by the app with no channel; its code was shown nowhere, so only the app's own card "
        "can approve it. Refusing works from here: --no"
    ),
    "nowhere": (
        "the process that asked had no terminal and no channel, so its code was shown nowhere and "
        "it cannot be approved — only refused (--no) or left to time out. Set "
        "CHIMERA_APPROVAL_WEBHOOK so the next one reaches you"
    ),
}


secrets_app = typer.Typer(help="Keep provider keys in the OS vault instead of a file.", no_args_is_help=True)
app.add_typer(secrets_app, name="secrets")


@secrets_app.command("list")
def secrets_list() -> None:
    """What the OS vault holds — names only, never values.

    Printing a secret would put it in this terminal's scrollback, in any screenshot of it, and in
    whatever recorded the session, which undoes the reason for having a vault.
    """
    from chimera.config_vault import available, stored

    if not available():
        console.print(
            "[yellow]no OS vault on this machine[/yellow] — install the extra with "
            r"[bold]pip install 'chimera-agent\[secrets]'[/bold], or keep using .env "
            "(a container or a headless server usually has no keychain, and that is fine)."
        )
        return
    guardados = stored()
    if not guardados:
        console.print("[dim]the vault holds no Chimera credentials[/dim]")
        return
    for nome in guardados:
        console.print(f"  {nome}")
    console.print("[dim]values are never printed[/dim]")


@secrets_app.command("set")
def secrets_set(
    name: str = typer.Argument(..., help="e.g. OPENROUTER_API_KEY"),
    value: str = typer.Option(None, "--value", help="Omit to be prompted without echo."),
) -> None:
    """Put one credential in the OS vault.

    Prompted without echo by default, and that is not politeness: a key typed as an argument lands
    in the shell history of every machine it is typed on, which is the kind of file this command
    exists to stop using.
    """
    from chimera.config_vault import STORABLE, available, store

    if name.upper() not in STORABLE:
        console.print(f"[yellow]{name} is not a credential this vault stores[/yellow]")
        console.print("[dim]storable: " + ", ".join(STORABLE) + "[/dim]")
        raise typer.Exit(code=1)
    if not available():
        console.print("[yellow]no OS vault on this machine[/yellow] — see `chimera secrets list`.")
        raise typer.Exit(code=1)
    if value is None:
        value = typer.prompt(f"{name.upper()}", hide_input=True)
    if not store(name, value):
        console.print("[red]the vault refused to store it[/red] (locked, or the prompt was cancelled)")
        raise typer.Exit(code=1)
    console.print(f"[green]stored[/green] {name.upper()} — the environment still wins over it")


@secrets_app.command("rm")
def secrets_rm(name: str = typer.Argument(..., help="The credential to forget.")) -> None:
    """Remove one credential from the OS vault."""
    from chimera.config_vault import forget

    if not forget(name):
        console.print(f"[yellow]{name.upper()} was not in the vault[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]forgot[/green] {name.upper()}")


cron_app = typer.Typer(help="Manage scheduled jobs (crons and event SOPs).", no_args_is_help=True)
app.add_typer(cron_app, name="cron")


def _cron_store() -> CronStore:
    from chimera.scheduler import CronStore

    path = get_settings().home / "scheduler" / "jobs.json"
    return CronStore(path)


@cron_app.command("list")
def cron_list() -> None:
    """List scheduled jobs."""
    from chimera.scheduler.delivery import webhook_host_only

    store = _cron_store()
    if len(store) == 0:
        console.print("[dim]no scheduled jobs[/dim]")
        return
    table = Table(title="Scheduled jobs", show_header=True, header_style="bold")
    for col in ("id", "name", "trigger", "schedule", "by", "enabled"):
        table.add_column(col)
    for job in store.list():
        table.add_row(
            job.id, job.name, job.trigger, job.schedule, job.created_by, str(job.enabled)
        )
    console.print(table)

    # Lines, not columns, for the same width reason as the failure line below — and only for the
    # jobs that differ from the default, so a crontab nobody tuned prints exactly what it did.
    for job in store.list():
        extras = []
        if job.notify != "always":
            extras.append(f"notify={job.notify}")
        if job.tools is not None:
            extras.append(f"tools={','.join(job.tools) or '(none)'}")
        if job.deliver_to:
            # The host only: the rest of a webhook URL is the credential to post into the channel.
            extras.append(f"deliver_to={webhook_host_only(job.deliver_to)}")
        if extras:
            console.print(f"  [cyan]{job.id}[/cyan] [dim]{' · '.join(extras)}[/dim]")

    # A line, not a column. This table was already at its width budget with six columns; a seventh
    # truncated the name, which is the column people read — and a truncated warning is a warning
    # somebody scrolls past. What this has to fix is that `enabled` and `schedule` together read as
    # health while saying nothing about whether the last dispatch won.
    falhando = [job for job in store.list() if job.enabled and job.consecutive_failures]
    if falhando:
        pior = max(job.consecutive_failures for job in falhando)
        console.print(
            f"[red]{len(falhando)} enabled job(s) failing[/red] "
            f"[dim](worst: {pior} in a row) — `chimera cron doctor`[/dim]"
        )


@cron_app.command("doctor")
def cron_doctor(
    grace_minutes: float = typer.Option(
        10.0, "--grace", help="How late a job may be before it counts as missed."
    ),
    check: bool = typer.Option(
        False, "--check",
        help="Exit 1 when a job is late or failing, so a watcher outside Chimera alerts only then.",
    ),
) -> None:
    """Ask the schedule what it is not telling you: what never ran, and what ran and lost.

    Every other honesty mechanism here sits downstream of a run having happened. This is the one
    question about the run that did not — and about the one that happens on time, forever, and
    fails every time, which looks healthier than the first from any field that existed before.

    It is a question, not a watcher: nothing notices while this process is down, for the same
    reason a crashed process cannot log its own crash. What it gives you is an honest answer the
    moment you ask.
    """
    import time

    from chimera.scheduler.engine import Scheduler
    from chimera.scheduler.watchdog import (
        default_heartbeat_path,
        infer_max_gap,
        watch_daemon,
        watch_tick_seconds,
    )

    sched = Scheduler(_cron_store())
    now = time.time()

    # The daemon's own sign of life, read BEFORE the jobs: a dead daemon with a daily job looks
    # healthy for ~23 hours from the jobs alone (the job is not yet late), and that window is
    # exactly what the heartbeat closes. The ceiling is derived from the beat's own tick
    # interval — three ticks of headroom — and printed, so a reader can disagree with the
    # number rather than wonder where it came from. No beat at all is "nothing to say", not
    # "dead": a daemon that has never run left no evidence either way.
    beat_path = default_heartbeat_path(get_settings().home)
    # The ceiling is derived from the beat's own tick interval BEFORE the verdict — the verdict
    # is judged against it, not shown beside it. A beat without an interval yields 0, which
    # reads as "no number": the verdict is `unknown`, and the CLI says so in words.
    intervalo = watch_tick_seconds(beat_path)
    teto = infer_max_gap(intervalo) if intervalo > 0 else None
    watch = watch_daemon(beat_path, now=now, max_gap_seconds=teto)
    if watch.verdict == "none":
        console.print("[dim]daemon: no heartbeat on record — `chimera serve --cron` may never "
                      "have run here, so there is nothing to say about it.[/dim]")
    elif watch.verdict == "unknown":
        console.print(
            f"[dim]daemon: heartbeat {watch.age_seconds:.0f}s old (no tick interval on record, "
            f"so freshness cannot be judged).[/dim]"
        )
    elif watch.verdict == "stale":
        console.print(
            f"[red]daemon: heartbeat is {watch.age_seconds:.0f}s old — older than "
            f"{teto:.0f}s (3 ticks of its own {intervalo:.0f}s interval). "
            f"The daemon is very likely dead.[/red]"
        )
        console.print(
            "[dim]This is about the daemon, not the jobs: check that `chimera serve --cron` "
            "(or the app) is up and has been.[/dim]"
        )
    else:
        console.print(
            f"[green]daemon: alive[/green] [dim](heartbeat {watch.age_seconds:.0f}s old, "
            f"ceiling {teto:.0f}s)[/dim]"
        )

    atrasados = sched.overdue(now, grace=grace_minutes * 60)
    falhando = sched.failing(at_least=1)

    if atrasados:
        console.print(f"[yellow]{len(atrasados)} job(s) due and never dispatched:[/yellow]")
        for job, behind in atrasados:
            console.print(f"  [cyan]{job.id}[/cyan] {job.name} — [yellow]{_ago(behind)} late[/yellow]")
        console.print(
            "[dim]Nothing ran. That is about the daemon, not the jobs: check that `chimera serve "
            "--cron` is up and has been.[/dim]"
        )

    if falhando:
        console.print(f"[red]{len(falhando)} job(s) dispatched on time and failing:[/red]")
        for job in falhando:
            console.print(
                f"  [cyan]{job.id}[/cyan] {job.name} — [red]{job.last_status} "
                f"x{job.consecutive_failures}[/red]: {job.last_error or ''}"
            )
        console.print("[dim]These ran. That is about the jobs, not the daemon.[/dim]")

    if not atrasados and not falhando:
        console.print("[green]every enabled job is on schedule and its last dispatch won[/green]")
        console.print(
            "[dim]Said about what this can see: a job that has never been due yet has nothing to "
            "report, and nothing here watches while this process is not running.[/dim]"
        )
    elif check:
        # The exit code is what a watcher outside Chimera reads (chimera-agent#26). Without it the
        # host-cron line in docs/deploy.md mailed the same report every thirty minutes whether or
        # not anything was wrong, which trains the reader to stop opening it.
        raise typer.Exit(1)

    # A stale heartbeat is a daemon verdict, and the exit code is the watcher's only ear: a dead
    # daemon with a daily job produces no overdue row for ~23 hours, so without this the
    # host-cron line in docs/deploy.md stays silent through exactly the outage it exists to
    # catch. Checked after the job exit so a job problem is not masked by a daemon one — both
    # exit 1, and the report above already names both.
    if check and watch.verdict == "stale":
        raise typer.Exit(1)


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


@cron_app.command("add")
def cron_add(
    name: str = typer.Argument(..., help="A human-readable name."),
    schedule: str = typer.Argument(..., help="Cron expression, or an event/webhook name."),
    action: str = typer.Argument(..., help="What to do (task description / skill)."),
    event: bool = typer.Option(False, "--event", help="Treat SCHEDULE as an event name."),
    webhook: bool = typer.Option(False, "--webhook", help="Fire on POST /webhook/<SCHEDULE> (needs 'chimera serve')."),
    verify: str = typer.Option(
        "", "--verify",
        help="Gate: shell command run in the job's folder after the dispatch (exit 0 to keep the "
             "work, non-zero to revert it). Empty = no gate, which is the previous behaviour.",
    ),
    max_attempts: int = typer.Option(
        1, "--max-attempts",
        help="Attempts per dispatch. Worth raising only with --verify: without a gate nothing can "
             "tell a failed attempt from a finished one.",
    ),
    notify: str = typer.Option(
        "always", "--notify",
        help="When the answer is posted to the job's destination: always (every answer except "
             "the job's own 'nothing new' reply), on_change (skip an answer identical to the last "
             "one delivered), or failures_only. The result file gets every answer either way. "
             "Not with --webhook: a webhook job answers through the chat gateway.",
    ),
    tools: str | None = typer.Option(
        None, "--tools",
        help="Comma-separated tools this job may use; the rest are removed from its registry. "
             "Omit for every tool (the previous behaviour). Refused with --webhook: a webhook "
             "job runs through the chat gateway, which does not apply the list.",
    ),
    deliver_to: str | None = typer.Option(
        None, "--deliver-to",
        help="Chat webhook URL (Discord or Slack) the job's answers are posted to, per --notify; "
             "a run that could not run or finish is announced there too. The URL is a credential "
             "and is never printed in full. Refused with --webhook: that job answers through "
             "the chat gateway.",
    ),
) -> None:
    """Add a cron, event- or webhook-triggered job.

    `--verify` is what turns a scheduled job into a run the harness governs. `CronJob` has carried
    the field since the harness landed and nothing could write it — not this command, not the HTTP
    route — so for every user the gate was permanently unarmed.
    """
    import time
    import urllib.parse

    from chimera.scheduler import Scheduler
    from chimera.scheduler.models import Notify

    modos: dict[str, Notify] = {
        "always": "always", "on_change": "on_change", "failures_only": "failures_only"
    }
    if notify not in modos:
        console.print(f"[red]--notify must be one of: {', '.join(modos)}[/red]")
        raise typer.Exit(code=1)
    modo = modos[notify]
    lista = None if tools is None else [t.strip() for t in tools.split(",") if t.strip()]
    destino = (deliver_to or "").strip() or None
    if destino is not None:
        # Checked here, at the keyboard, rather than discovered at 07:00 by a delivery that refuses
        # the scheme. Nothing of the URL is echoed back: its path is the channel's secret.
        partes = urllib.parse.urlparse(destino)
        if partes.scheme not in ("http", "https") or not partes.hostname:
            console.print("[red]--deliver-to must be an http(s) webhook URL with a host[/red]")
            raise typer.Exit(code=1)
        if webhook:
            console.print(
                "[red]--deliver-to is not taken with --webhook: that job answers through the chat "
                "gateway, which does not read it[/red]"
            )
            raise typer.Exit(code=1)

    sched = Scheduler(_cron_store())
    # Passed by name rather than unpacked from a dict: a `**kwargs` here type-erases both fields,
    # and these are exactly the two that decide whether the run is governed.
    if webhook:
        try:
            job = sched.schedule_webhook(
                name, schedule, action, verify=verify, max_attempts=max_attempts,
                notify=modo, tools=lista,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
    elif event:
        job = sched.schedule_event(
            name, schedule, action, deliver_to=destino, verify=verify,
            max_attempts=max_attempts, notify=modo, tools=lista,
        )
    else:
        try:
            job = sched.schedule_cron(
                name, schedule, action, now=time.time(), deliver_to=destino,
                verify=verify, max_attempts=max_attempts, notify=modo, tools=lista,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
    console.print(f"[green]added[/green] job {job.id} ({job.name})")


@cron_app.command("remove")
def cron_remove(job_id: str = typer.Argument(..., help="The job id to remove.")) -> None:
    """Remove a scheduled job by id."""
    store = _cron_store()
    if job_id not in store:
        console.print(f"[yellow]no job with id {job_id}[/yellow]")
        raise typer.Exit(code=1)
    store.remove(job_id)
    console.print(f"[green]removed[/green] {job_id}")


@cron_app.command("enable")
def cron_enable(job_id: str = typer.Argument(..., help="The job id to enable.")) -> None:
    """Enable a job (e.g. an agent-proposed one) and schedule its next run."""
    import time

    from chimera.scheduler import Scheduler

    store = _cron_store()
    if job_id not in store:
        console.print(f"[yellow]no job with id {job_id}[/yellow]")
        raise typer.Exit(code=1)
    Scheduler(store).enable(job_id, now=time.time())
    console.print(f"[green]enabled[/green] {job_id}")


@cron_app.command("disable")
def cron_disable(job_id: str = typer.Argument(..., help="The job id to disable.")) -> None:
    """Disable a job without deleting it."""
    from chimera.scheduler import Scheduler

    store = _cron_store()
    if job_id not in store:
        console.print(f"[yellow]no job with id {job_id}[/yellow]")
        raise typer.Exit(code=1)
    Scheduler(store).disable(job_id)
    console.print(f"[green]disabled[/green] {job_id}")


@cron_app.command("kill")
def cron_kill(job_id: str = typer.Argument(..., help="The job id to stop.")) -> None:
    """Stop a job's running (or next) dispatch — one run, not the schedule.

    `disable` takes the job off the clock; `kill` answers the other question: the job is running
    RIGHT NOW and must stop. The daemon's worker polls the flag between steps, the dispatch it
    stops deletes it, and the run ends `cancelled` — which counts as neither a failure nor a
    success, so a kill cannot ride the failure counter into the brake.
    """
    import time

    from chimera.scheduler import Scheduler

    store = _cron_store()
    if job_id not in store:
        console.print(f"[yellow]no job with id {job_id}[/yellow]")
        raise typer.Exit(code=1)
    stopped = Scheduler(store).kill(job_id, now=time.time())
    if stopped:
        console.print(f"[green]kill requested[/green] {job_id} — the running dispatch will stop")
    else:
        console.print(
            f"[yellow]not stopped[/yellow] {job_id} — the job is disabled, so nothing is running"
        )
        raise typer.Exit(code=1)


@cron_app.command("fire")
def cron_fire(
    event: str = typer.Argument(..., help="The event name to fire (as given to `cron add --event`)."),
    model: str = typer.Option(None, "--model", "-m", help="Model for the dispatched jobs."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per job."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root for tools."),
) -> None:
    """Run every job registered for an event.

    Event jobs had no dispatcher. `cron add --event deploy` accepted the job and `cron list` showed
    it enabled, but nothing in the package ever called `fire_event` — so the job simply never ran,
    and its silence was indistinguishable from that of a job whose time had not come. The cron
    trigger has the daemon and the webhook trigger has the webhook server; this is the third one's.

    Meant to be called from wherever the event actually happens — a git hook, a deploy step, a CI
    job. Dispatch is the same one the daemon uses, so a fired job behaves exactly like a scheduled
    one: same agent, same spend caps, same receipt.
    """
    import time

    from chimera.providers import LLMGateway
    from chimera.scheduler import Scheduler, make_agent_dispatch
    from chimera.scheduler.delivery import make_deliver, make_failure_notifier
    from chimera.scheduler.job_runner import make_run_job

    scheduler = Scheduler(_cron_store())
    if not scheduler.jobs_for_event(event):
        # Louder than an empty run: a typo in an event name is the likeliest way to sit waiting for
        # something that will never happen, and "0 jobs" printed in green reads like success.
        console.print(f"[yellow]no enabled job registered for event {event!r}[/yellow]")
        raise typer.Exit(code=1)

    settings = get_settings()
    run_job = make_run_job(
        settings=settings,
        backend=LLMGateway(),
        workspace=Path(workspace).resolve(),
        model=model,
        max_steps=max_steps,
        usage_path=settings.home / "usage.jsonl",
        warn=lambda linha: console.print(f"[yellow]{linha}[/yellow]"),
    )
    # Through `make_agent_dispatch`, not straight to `fire_event`. It is what turns a `JobOutcome`
    # into the status the scheduler records — so a gate that rejected the work reads as `rejected`
    # rather than as success — and it is where delivery happens. Without it a fired job would run,
    # be recorded as ok whatever its gate said, and deliver nothing: a second dispatch path that
    # quietly behaves differently from the daemon's is worse than no second path.
    def _sem_job(_task: str) -> str:  # pragma: no cover - unreachable while run_job is given
        raise AssertionError("cron fire always has the job; run_task should never be reached")

    deliver = make_deliver(
        settings.home / "scheduler" / "cron_results.jsonl",
        warn=lambda linha: console.print(f"[yellow]{linha}[/yellow]"),
    )
    agora = time.time()
    # Held while it runs, like a clock job (`chimera/core/keep_awake.py`; nothing unless
    # CHIMERA_KEEP_AWAKE is on). A deploy hook firing an hour-long job should not lose it to sleep.
    from chimera.core.keep_awake import holding

    ran = scheduler.fire_event(
        event, agora, holding("cron", make_agent_dispatch(_sem_job, deliver, run_job=run_job))
    )
    # The same failure notice the daemon gives a scheduled job, posted inline: this is a one-shot
    # command, and a notice handed to a background thread would die with the process before it
    # reached the network. There is no tick here to hold up.
    notices = make_failure_notifier(
        warn=lambda linha: console.print(f"[yellow]{linha}[/yellow]"),
        enabled=lambda: settings.cron_notify_failures,
        post=lambda enviar: enviar(),
    )
    for job in ran:
        if notices(job, agora):
            scheduler.store.add(job)
    for job in ran:
        cor = "green" if job.last_status == "ok" else "red"
        console.print(f"[{cor}]{job.last_status}[/{cor}] {job.name} ({job.id})")
        if job.last_error:
            console.print(f"  {job.last_error}")


@cron_app.command("learn")
def cron_learn(
    min_occurrences: int = typer.Option(3, "--min", help="Min repeats to propose."),
    schedule: str = typer.Option(None, "--schedule", help="Override the suggested cron schedule."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Create every proposal without prompting."),
) -> None:
    """Propose crons from recurring tasks and create the ones you confirm.

    Each proposal is shown for explicit confirmation (the human-in-the-loop approval
    that keeps automation creation under control); confirmed jobs are validated and
    created enabled. ``--yes`` confirms all (use deliberately).
    """
    from chimera.evolution import ExperienceBuffer
    from chimera.governance.validator import ScheduleValidator
    from chimera.scheduler import CronLearner, Scheduler

    settings = get_settings()
    history = [e.task for e in ExperienceBuffer(settings.home / "experience.json").all()]
    learner = CronLearner(min_occurrences=min_occurrences)
    proposals = learner.analyze(history)
    if not proposals:
        console.print("[dim]no recurring tasks found in history[/dim]")
        return

    scheduler = Scheduler(_cron_store())
    validator = ScheduleValidator()
    created = 0
    for proposal in proposals:
        sched = schedule or proposal.suggested_schedule
        if not validator.validate(sched).accepted:
            console.print(f"[yellow]skip[/yellow] {proposal.name}: invalid schedule '{sched}'")
            continue
        import time

        from chimera.scheduler import describe_schedule, upcoming_firings

        next_times = ", ".join(
            firing.strftime("%Y-%m-%d %H:%M %Z")
            for firing in upcoming_firings(sched, time.time())
        )
        summary = (
            f"[cyan]{proposal.name}[/cyan] (seen {proposal.occurrences}x) → '{sched}'\n"
            f"  {describe_schedule(sched)}\n  Next: {next_times}"
        )
        console.print(summary)
        if yes or typer.confirm(f"Create cron {proposal.name} for: {proposal.action}?", default=False):
            job = learner.build_job(proposal, enabled=True, schedule=sched)
            scheduler.store.add(job)
            created += 1
            console.print(f"  [green]created[/green] {job.id} {job.name} (enabled)")
        else:
            console.print(f"  [dim]skipped[/dim] {proposal.name}")
    console.print(f"created {created} cron(s) of {len(proposals)} proposed.")


# --- report subcommands -------------------------------------------------------

report_app = typer.Typer(
    help="Reports counted by code — from this home's own logs, or read with the GitHub CLI — no model call.",
    no_args_is_help=True,
)
app.add_typer(report_app, name="report")


@report_app.command("weekly")
def report_weekly(
    print_now: bool = typer.Option(
        False, "--print",
        help="Print the last 7 days' review now instead of proposing the weekly job. Reads only.",
    ),
    deliver_to: str | None = typer.Option(
        None, "--deliver-to",
        help="Chat webhook URL (Discord or Slack) the weekly job posts to. Stored on the proposal; "
             "never printed in full.",
    ),
    lang: str | None = typer.Option(
        None, "--lang",
        help="pt or en. Default: the owner's identity language (Portuguese unless it names another).",
    ),
) -> None:
    """Weekly review: spend, runs, approvals and failing jobs over the last 7 days.

    Every number is computed by code from the same logs the app's screens read (`usage.jsonl`,
    `runs.jsonl`, `approvals/history.jsonl`, `scheduler/jobs.json`); no model writes or restates
    any of them. Without `--print` this registers the weekly job — Mondays 09:00, DISABLED — once:
    it runs only after `chimera cron enable <id>`, and posts only where `--deliver-to` says.
    """
    import time

    from chimera.scheduler import Scheduler
    from chimera.scheduler.delivery import webhook_host_only
    from chimera.scheduler.weekly_review import (
        Lang,
        build_weekly_review,
        owner_lang,
        propose,
        render_weekly_review,
        valid_webhook,
    )

    escolhida: Lang | None
    if lang is None:
        escolhida = None
    elif lang in ("pt", "en"):
        escolhida = "pt" if lang == "pt" else "en"
    else:
        console.print("[red]--lang must be pt or en[/red]")
        raise typer.Exit(code=1)

    settings = get_settings()
    if print_now:
        if deliver_to is not None:
            console.print("[red]--deliver-to is for the weekly job; --print only prints[/red]")
            raise typer.Exit(code=1)
        texto = render_weekly_review(
            build_weekly_review(settings.home), escolhida or owner_lang(settings.home)
        )
        # Plain print, not rich: the text carries backticks and brackets that rich would read as
        # markup, and a number must reach the reader exactly as it was computed.
        print(texto)
        return

    destino = (deliver_to or "").strip() or None
    if destino is not None and not valid_webhook(destino):
        console.print("[red]--deliver-to must be an http(s) webhook URL with a host[/red]")
        raise typer.Exit(code=1)
    job, created = propose(
        Scheduler(_cron_store()), now=time.time(), deliver_to=destino, lang=escolhida
    )
    estado = "enabled" if job.enabled else "disabled"
    verbo = "proposed" if created else "already proposed"
    console.print(f"[green]{verbo}[/green] job {job.id} ({job.name}, '{job.schedule}', {estado})")
    console.print(
        f"  posts to: {webhook_host_only(job.deliver_to) if job.deliver_to else 'nowhere yet — the result log only'}"
    )
    if not job.enabled:
        console.print(f"  [dim]switch it on with: chimera cron enable {job.id}[/dim]")
    console.print("  [dim]see what it would say: chimera report weekly --print[/dim]")


@report_app.command("pr-watch")
def report_pr_watch(
    workspace: str = typer.Option(
        ".", "--workspace", "-w",
        help="The repository to watch (a folder inside a git checkout whose origin is on GitHub).",
    ),
    print_now: bool = typer.Option(
        False, "--print",
        help="Look now and print the summary instead of proposing the job. Reads only; remembers nothing.",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Look now and print what was found as JSON. Reads only; remembers nothing.",
    ),
    deliver_to: str | None = typer.Option(
        None, "--deliver-to",
        help="Chat webhook URL (Discord or Slack) the job posts to. Stored on the proposal; never printed in full.",
    ),
    lang: str | None = typer.Option(
        None, "--lang",
        help="pt or en. Default: the owner's identity language (Portuguese unless it names another).",
    ),
) -> None:
    """Pull request watch: failing checks and new comments on your open pull requests, and failed runs
    on the default branch — read with the GitHub CLI, never acted on.

    Without `--print`/`--json` this registers the watch for WORKSPACE as an hourly job, DISABLED,
    once per repository: it runs only after `chimera cron enable <id>`, posts only where
    `--deliver-to` says, and posts again only when the summary changes. Nothing is pushed, commented,
    merged or re-run, and other people's comments are quoted inside the data fence, as data.
    """
    import json as _json
    import time

    from chimera.scheduler import Scheduler
    from chimera.scheduler.delivery import webhook_host_only
    from chimera.scheduler.pr_watch import FIRST_LOOK_SECONDS, as_dict, collect, propose, render
    from chimera.scheduler.weekly_review import Lang, owner_lang, valid_webhook

    escolhida: Lang | None
    if lang is None:
        escolhida = None
    elif lang in ("pt", "en"):
        escolhida = "pt" if lang == "pt" else "en"
    else:
        console.print("[red]--lang must be pt or en[/red]")
        raise typer.Exit(code=1)
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        console.print(f"[red]--workspace {escape(str(root))} is not a folder[/red]")
        raise typer.Exit(code=1)

    settings = get_settings()
    if print_now or as_json:
        if deliver_to is not None:
            console.print("[red]--deliver-to is for the job; --print and --json only look[/red]")
            raise typer.Exit(code=1)
        try:
            report = collect(root, since=time.time() - FIRST_LOOK_SECONDS)
        except (RuntimeError, ValueError) as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(code=1) from exc
        # Plain print, not rich: quoted comments carry brackets rich would read as markup, and the
        # text must reach the reader exactly as it was rendered.
        if as_json:
            print(_json.dumps(as_dict(report), ensure_ascii=False, indent=2))
        else:
            print(render(report, escolhida or owner_lang(settings.home)))
        return

    destino = (deliver_to or "").strip() or None
    if destino is not None and not valid_webhook(destino):
        console.print("[red]--deliver-to must be an http(s) webhook URL with a host[/red]")
        raise typer.Exit(code=1)
    job, created = propose(
        Scheduler(_cron_store()), now=time.time(), workspace=str(root), deliver_to=destino,
        lang=escolhida,
    )
    estado = "enabled" if job.enabled else "disabled"
    verbo = "proposed" if created else "already proposed"
    console.print(f"[green]{verbo}[/green] job {job.id} ({job.name}, '{job.schedule}', {estado})")
    console.print(f"  watches: {escape(str(root))}")
    console.print(
        f"  posts to: {webhook_host_only(job.deliver_to) if job.deliver_to else 'nowhere yet — the result log only'}"
    )
    if not job.enabled:
        console.print(f"  [dim]switch it on with: chimera cron enable {job.id}[/dim]")
    console.print("  [dim]see what it would say: chimera report pr-watch --print[/dim]")
