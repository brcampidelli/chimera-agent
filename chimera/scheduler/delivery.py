"""Sending a scheduled job's answer somewhere a person will actually see it.

``CronJob.deliver_to`` has existed since the model did, and until now it appeared in exactly two
places in the codebase: its own field declaration, and a line copying it into the result file. No
code read it to deliver anything. A schedule wrote its answer to a JSONL nobody opens and that was
the whole of "delivery" — which is why an install could run a job every night for a week and its
owner never see one word of the output.

A webhook URL rather than a bot token, deliberately. A bot needs an application, a token, an invite
and a server the user administers; a webhook is a URL you copy out of a channel's settings, and it
is the difference between a feature every user of a desktop app can turn on and one only the author
of the app has set up.

**The URL is a credential.** Whoever holds it can post into that channel, so it is never logged in
full — :func:`webhook_host_only` is used on every path that reports a failure.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from chimera.scheduler.models import CronJob
from chimera.scheduler.surface import answer_fingerprint, is_nothing_new
from chimera.telemetry import get_logger

_log = get_logger("scheduler.delivery")

#: Discord rejects a message body over 2000 characters outright. Slack truncates around 4000 and
#: keeps going. Cutting at the smaller of the two means one rule for every destination, and a
#: refused delivery is worse than a shortened one — the answer is in the result file either way.
MAX_CHARS = 1900

#: A delivery must not hold the scheduler. Dispatch is sequential: a webhook host that hangs would
#: delay every other job that is due, which is the failure `job_timeout` exists to prevent and this
#: has no business reintroducing.
TIMEOUT_S = 10.0

#: The dispatch statuses that mean the job could not run or could not finish. Not ``rejected``: a
#: rejected run finished and has an answer, which the sink posts as it always has. Not
#: ``cancelled``: the person who ran ``cron kill`` knows.
UNFINISHED: tuple[str, ...] = ("error", "timeout", "budget")

#: One line per failure kind, written here and never derived from the error. The category is what
#: the reader acts on — a code fix, a slower provider, a number in the configuration — and the
#: detail lives where the owner already looks for it, behind their own login.
_REASON = {
    "error": "The run stopped with an error.",
    "timeout": "The run went past its time limit and was abandoned.",
    # Two raises carry this status and the line has to be true of both: the daily cap is checked
    # before the job starts (`job_runner`), and the job's own token budget, in hard mode, stops it
    # mid-run after it has already spent (`orchestration.budget`). "It did not run" was false for
    # the second. Telling them apart would mean reading the exception, which this module refuses.
    "budget": "A spend cap stopped it: the daily cap before it started, or the job's own budget "
    "during the run.",
}

#: Finished runs in a row (``ok`` or ``rejected``) that end an outage. Two, not one: a provider
#: that fails every other call gives exactly one success between two errors, and with one the
#: notice announced "running again" on every such success and the failure again on the next tick —
#: a job alternating ok/error posted 11 times in 12 ticks, more than before the notice existed,
#: and the brake never fires on it because each success resets the failure count. With two, a
#: sustained outage still costs one post when it starts and one when it ends; the price is that the
#: recovery line waits for the second good run, which for a daily job is a day.
RECOVERY_RUNS = 2

#: Where the detail is. Named rather than quoted: `cron doctor` prints `last_error` on the machine,
#: which is the right place for text this module refuses to post.
_WHERE = " The error is not posted here; `chimera cron doctor` on the host shows it."


@dataclass(frozen=True)
class Delivered:
    """What happened when we tried. ``ok=False`` is a report, never an exception upwards.

    The job already ran and its answer is already on disk; failing the job because a chat service
    was down would throw away work that succeeded, and would count against
    ``consecutive_failures`` as though the schedule itself were broken.
    """

    ok: bool
    detail: str = ""


def skip_reason(job: CronJob, answer: str, status: str = "ok") -> str:
    """Why this answer is NOT posted to the job's destination, or "" when it is.

    ``status`` is how the dispatch went: ``ok``, ``rejected`` by the job's own gate, or a run that
    did not finish (``error``, ``budget``, ``timeout``). A rejection is never skipped, under any
    ``notify``: the owner who chose quiet chose to hear only when something is wrong, and a job
    that fails the same way twice is still failing. Everything else follows :attr:`CronJob.notify`.

    A run that did not finish has no answer, only an exception, and the exception is never posted:
    its text can carry a provider's response body, a path, a value the job was handling, or a
    sentence some page planted for a model to repeat — and a chat channel is read by people who
    were never meant to see any of that. It is recorded here and announced by
    :func:`make_failure_notifier`, which posts the job, the status and the time, once per outage.
    """
    if status in UNFINISHED:
        return "a run that did not finish is announced by a short failure notice, never by its error"
    if status != "ok":
        return ""
    if job.notify == "failures_only":
        return "notify=failures_only, and this run did not fail"
    if is_nothing_new(answer):
        return "the job reported nothing new"
    if job.notify == "on_change" and job.last_delivered_hash == answer_fingerprint(answer):
        return "notify=on_change, and the answer is the same as the last one delivered"
    return ""


def make_deliver(
    results_path: Path,
    *,
    warn: Callable[[str], None] | None = None,
    send: Callable[[str, str], Delivered] | None = None,
) -> Callable[..., None]:
    """The sink a cron daemon hands its answers to: the file always, the webhook when there is one.

    A module function rather than a closure inside the ``chimera app`` command, because the defect
    this replaces was never in a mechanism — it was in the WIRING. ``deliver_to`` was declared,
    copied into the result record, and read by nothing. A test of the sending mechanism would have
    passed the whole time. So this is reachable, and there is a test asserting it actually sends.

    ``warn`` receives one line when a delivery fails; ``send`` exists so a test can drive the
    failure path without a socket.

    The returned sink takes ``(job, answer, status="ok")``. What it does NOT post is decided by
    :func:`skip_reason` and written into the result record as ``skipped`` — a suppressed answer is
    still an answer, and the record is where the owner goes to find out what the job said on the
    days it said nothing to them.
    """
    enviar = send or deliver_to_webhook

    def deliver(job: CronJob, answer: str, status: str = "ok") -> None:
        entrega: Delivered | None = None
        motivo = skip_reason(job, answer, status)
        if job.deliver_to and not motivo:
            entrega = enviar(job.deliver_to, f"**{job.name}**\n{answer}")
            if not entrega.ok and warn is not None:
                # Said out loud rather than swallowed: a delivery that fails silently is
                # indistinguishable from a job that never ran.
                warn(f"cron '{job.name}': delivery failed — {entrega.detail}")
        # Assigned on every call, true or false, so it never carries over from an earlier run: the
        # failure notice reads it to know whether the channel already saw this run's answer.
        job._answer_posted = entrega is not None and entrega.ok
        # The fingerprint moves only when the answer actually reached its destination (or there is
        # no destination, and the record IS the delivery). A post that failed was not seen, so the
        # same answer next time is still news to the person it was for. And only for a successful
        # run: a failure is posted as a failure, and a later success with the same text is a
        # different message to its reader — skipping it as "the same as last time" would hide the
        # one run that worked behind the one that did not.
        if (
            job.notify == "on_change"
            and status == "ok"
            and not motivo
            and (entrega is None or entrega.ok)
        ):
            job.last_delivered_hash = answer_fingerprint(answer)

        results_path.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, object] = {
            "at": time.time(),
            "id": job.id,
            "name": job.name,
            "action": job.action,
            "deliver_to": job.deliver_to,
            "answer": answer,
        }
        if status != "ok":
            record["status"] = status
        if entrega is not None:
            record["delivered"] = entrega.ok
            record["delivery_detail"] = entrega.detail
        # Only when there was somewhere to post it: `skipped` means "held back from the destination
        # on purpose", and a job with no destination held nothing back. Recorded there it would read
        # as a decision that never had anything to decide.
        if motivo and job.deliver_to:
            record["skipped"] = motivo
        with results_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    return deliver


def _when(at: float) -> str:
    """The tick's time in the machine's own zone, with the offset, to the minute.

    Through UTC rather than a naive ``fromtimestamp`` for the reason the engine's ``_next_after``
    gives: Windows raises on the local offset of a small epoch, and the tests pass small epochs.
    """
    return datetime.fromtimestamp(at, tz=UTC).astimezone().isoformat(timespec="minutes")


@dataclass(frozen=True)
class NoticeState:
    """What the owner's channel has been told about a job's failures — the three fields on the job.

    ``told`` is the open outage by its latest kind (``""`` for none), ``kinds`` every kind already
    announced in it, ``healthy`` the finished runs since its last failure. Persisted on the job by
    the notifier, so a restart in the middle of an outage resumes it instead of announcing it again.
    """

    told: str = ""
    kinds: tuple[str, ...] = ()
    healthy: int = 0

    @classmethod
    def of(cls, job: CronJob) -> NoticeState:
        return cls(job.failure_notice, tuple(job.failure_notice_told), job.failure_notice_healthy)


def failure_notice(job: CronJob, at: float) -> tuple[NoticeState, str | None]:
    """The notice state this run leaves the job in, and the line to post — None for nothing.

    Read from what the engine already recorded on the job (``last_status``, ``enabled``,
    ``disabled_by``, ``consecutive_failures``) against :class:`NoticeState`, what the owner has
    been told. An outage is announced, and the line is posted, only:

    * when it opens — a failure while none is open;
    * when a failure kind not yet announced in it arrives — ``error`` and ``budget`` need opposite
      responses (a code fix, a number in the configuration), so the first ``budget`` after an
      ``error`` is news; the tenth alternation between them is not;
    * into ``brake``, the run on which the engine switched the job off: it will not run again until
      a person says so, which is a different fact from "it is failing";
    * on the first failure after the brake — a person turned the job back on, and it still fails;
    * when it ends, after :data:`RECOVERY_RUNS` finished runs in a row — once, and only if the
      channel did not just see the closing run's own answer (``notify="always"``, or a
      ``rejected`` run, whose answer the sink posts under every mode). That answer IS the news that
      the job runs again; a line on top of it would say it twice.

    A finished run inside an open outage only counts towards its end; a failure inside it resets
    that count. A ``cancelled`` run changes nothing: a person stopped it.

    Never reads ``last_error``. That is the point: the line is assembled from fields this code
    wrote, so nothing a provider, a tool or a web page said can reach the channel through it.
    """
    antes = NoticeState.of(job)
    status = job.last_status
    if not job.enabled and job.disabled_by == "brake":
        tipo = "brake"
    elif status in UNFINISHED:
        tipo = str(status)
    elif status in ("ok", "rejected"):
        tipo = ""
    else:
        return antes, None
    quando = _when(at)

    if tipo == "":
        if not antes.told:
            return antes, None
        saudavel = antes.healthy + 1
        if saudavel < RECOVERY_RUNS:
            return NoticeState(antes.told, antes.kinds, saudavel), None
        if job._answer_posted:
            return NoticeState(), None
        return NoticeState(), (
            f"**{job.name}** is running again: {saudavel} runs in a row have finished, the last at "
            f"{quando}."
        )

    kinds = antes.kinds if tipo in antes.kinds else (*antes.kinds, tipo)
    depois = NoticeState(tipo, kinds, 0)
    if tipo == "brake":
        if antes.told == "brake":
            return depois, None
        return depois, (
            f"**{job.name}** was switched off at {quando} (`brake`): it failed "
            f"{job.consecutive_failures} times in a row. `chimera cron enable {job.id}` turns it "
            "back on." + _WHERE
        )
    if tipo in antes.kinds and antes.told != "brake":
        return depois, None
    onde = "" if tipo == "budget" else _WHERE
    return depois, (
        f"**{job.name}** could not finish: `{tipo}` at {quando}. {_REASON[tipo]}{onde}"
    )


def _off_the_tick(fn: Callable[[], None]) -> None:
    """Run ``fn`` on a daemon thread. A notice that waited on the network inside the tick would hold
    every job due after it, for up to :data:`TIMEOUT_S` each — the stall ``job_timeout`` exists to
    prevent, reintroduced by the code that reports on it."""
    threading.Thread(target=fn, daemon=True, name="chimera-cron-notice").start()


def make_failure_notifier(
    *,
    send: Callable[[str, str], Delivered] | None = None,
    warn: Callable[[str], None] | None = None,
    enabled: Callable[[], bool] = lambda: True,
    post: Callable[[Callable[[], None]], None] | None = None,
) -> Callable[[CronJob, float], bool]:
    """The daemon's ``on_outcome``: tell the job's destination that it could not run, once per outage.

    Here and not in the engine. ``Scheduler._brake`` says it in writing — the engine takes no clock
    and no I/O — and two of the four outcomes this reports (a timeout, the brake) are decided by
    the engine OUTSIDE the dispatch, so the result sink never hears of them, while the other two
    (``error``, ``budget``) reach the sink only for a job whose ``notify`` is not ``always``. The
    daemon has the clock and the list of jobs it just ran, with their outcome already recorded;
    that is the one place all four are visible for every job.

    ``enabled`` is asked on every call (``CHIMERA_CRON_NOTIFY_FAILURES``), so switching it off
    applies from the next tick. Off posts nothing and moves no state, so switching it back on
    announces a failure still in progress rather than staying quiet about it.

    The state moves BEFORE the post, on the tick, and the post runs off it (``post``; a daemon
    thread by default). So a post that fails is not retried on the next tick: retrying until it
    lands is what a dead webhook would turn into one message per tick, the exact noise this
    replaces. The failure stays in ``jobs.json`` and ``cron doctor``, and the post failure is said
    out loud through ``warn``.

    Returns whether the job's state changed, so the caller can persist it.
    """
    enviar = send or deliver_to_webhook
    correr = post or _off_the_tick

    def notify(job: CronJob, at: float) -> bool:
        if not job.deliver_to or not enabled():
            job._answer_posted = False
            return False
        depois, texto = failure_notice(job, at)
        # Read once, for this run: the mark describes one dispatch and must not speak for the next.
        job._answer_posted = False
        if depois == NoticeState.of(job):
            return False
        job.failure_notice = depois.told
        job.failure_notice_told = list(depois.kinds)
        job.failure_notice_healthy = depois.healthy
        if texto is not None:
            url, nome = job.deliver_to, job.name

            def _enviar() -> None:
                entrega = enviar(url, texto)
                if not entrega.ok and warn is not None:
                    warn(f"cron '{nome}': failure notice not delivered — {entrega.detail}")

            correr(_enviar)
        return True

    return notify


def webhook_host_only(url: str) -> str:
    """A webhook URL with its secret path removed, safe to put in a log or an error message.

    Named `redact` until it collided with :func:`chimera.core.redact.redact`, which takes arbitrary
    text and masks credentials inside it. Two functions with one name and different contracts is how
    a caller reaches for the wrong one — and the wrong one here would leave the whole path in the
    log, because a webhook URL has no credential SHAPE in it: the path IS the secret.
    """
    try:
        parts = urllib.parse.urlparse(url)
    except ValueError:
        return "<unparseable url>"
    if not parts.hostname:
        return "<no host>"
    return f"{parts.scheme}://{parts.hostname}/…"


def payload_for(url: str, text: str) -> dict[str, str]:
    """The body each service expects.

    Slack reads ``text``; Discord reads ``content``. They are otherwise the same shape, so one
    function covers both and anything Discord-compatible (which most self-hosted chat webhooks are).
    """
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host.endswith("slack.com"):
        return {"text": text}
    return {"content": text}


def clip(text: str, limit: int = MAX_CHARS) -> str:
    """Shorten to ``limit`` characters and say so, rather than being cut off mid-sentence.

    The marker matters more than the saved characters: a message that simply stops looks like the
    agent stopped, and that is a different thing to go and investigate.
    """
    if len(text) <= limit:
        return text
    marca = "\n… (truncated — the full answer is in cron_results.jsonl)"
    return text[: limit - len(marca)] + marca


def deliver_to_webhook(url: str, text: str, *, timeout: float = TIMEOUT_S) -> Delivered:
    """POST ``text`` to a chat webhook. Returns what happened; never raises.

    Only http(s): the field is a plain string on a job that an agent can propose, and a scheme like
    ``file:`` would turn a delivery address into a local read. Agent-proposed jobs already arrive
    disabled, so this is the second lock rather than the first.
    """
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https"):
        return Delivered(False, f"refusing scheme {parts.scheme!r}: only http and https are sent to")
    if not parts.hostname:
        return Delivered(False, "no host in the delivery URL")

    corpo = json.dumps(payload_for(url, clip(text))).encode("utf-8")
    req = urllib.request.Request(
        url, data=corpo, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Delivered(True, f"HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        # The body carries the reason (bad token, unknown channel, rate limit) and is worth keeping,
        # but it is written by a remote service — bounded before it goes anywhere near a log line.
        detalhe = (exc.read().decode("utf-8", "replace") or "")[:200].strip()
        _log.warning("delivery to %s refused: HTTP %s", webhook_host_only(url), exc.code)
        return Delivered(False, f"HTTP {exc.code}: {detalhe}" if detalhe else f"HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001 — a chat outage must not fail a job that worked
        _log.warning("delivery to %s failed: %s", webhook_host_only(url), type(exc).__name__)
        return Delivered(False, f"{type(exc).__name__}: {exc}")
