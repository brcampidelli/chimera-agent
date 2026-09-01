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
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from chimera.scheduler.models import CronJob
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


@dataclass(frozen=True)
class Delivered:
    """What happened when we tried. ``ok=False`` is a report, never an exception upwards.

    The job already ran and its answer is already on disk; failing the job because a chat service
    was down would throw away work that succeeded, and would count against
    ``consecutive_failures`` as though the schedule itself were broken.
    """

    ok: bool
    detail: str = ""


def make_deliver(
    results_path: Path,
    *,
    warn: Callable[[str], None] | None = None,
    send: Callable[[str, str], Delivered] | None = None,
) -> Callable[[CronJob, str], None]:
    """The sink a cron daemon hands its answers to: the file always, the webhook when there is one.

    A module function rather than a closure inside the ``chimera app`` command, because the defect
    this replaces was never in a mechanism — it was in the WIRING. ``deliver_to`` was declared,
    copied into the result record, and read by nothing. A test of the sending mechanism would have
    passed the whole time. So this is reachable, and there is a test asserting it actually sends.

    ``warn`` receives one line when a delivery fails; ``send`` exists so a test can drive the
    failure path without a socket.
    """
    enviar = send or deliver_to_webhook

    def deliver(job: CronJob, answer: str) -> None:
        entrega: Delivered | None = None
        if job.deliver_to:
            entrega = enviar(job.deliver_to, f"**{job.name}**\n{answer}")
            if not entrega.ok and warn is not None:
                # Said out loud rather than swallowed: a delivery that fails silently is
                # indistinguishable from a job that never ran.
                warn(f"cron '{job.name}': delivery failed — {entrega.detail}")

        results_path.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, object] = {
            "at": time.time(),
            "id": job.id,
            "name": job.name,
            "action": job.action,
            "deliver_to": job.deliver_to,
            "answer": answer,
        }
        if entrega is not None:
            record["delivered"] = entrega.ok
            record["delivery_detail"] = entrega.detail
        with results_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    return deliver


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
