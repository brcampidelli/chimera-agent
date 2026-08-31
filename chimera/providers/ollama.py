"""Which models the local Ollama actually has pulled.

Every field on the Settings screen that names an Ollama model — the default model, the three rungs,
the completion model — was a free-text box the user filled from memory. A tag that is not pulled
does not fail at save time: it fails on the first call, mid-run, as a 404 from a server the user
believed was ready. The answer was one HTTP call away the whole time, against a URL the app already
stores and already hands to LiteLLM.

Three decisions worth stating, because each is about not lying:

**"Ollama did not answer" and "Ollama has nothing pulled" are different facts and get different
fields.** Collapsing them into an empty list is the failure this module exists to prevent: a picker
showing nothing reads as *you have no models*, which is a claim about the user's machine that we did
not make and cannot support when nothing answered the door. So ``reachable`` carries the first fact
and ``models`` the second, and a caller has to look at both.

**The failure is a WORD, not a sentence.** ``reason`` is a machine token the client translates, the
same shape and for the same reason as :class:`~chimera.api.posture.PostureFacts`: the app ships ten
languages, and a server that returned English prose would make the one line explaining why a feature
is unavailable the one line the user cannot read.

**The timeout is short and deliberate, and CONNECTING is budgeted apart from ANSWERING.** This is
asked from a settings panel, and the configured URL may point at a machine that is off, asleep, or
on the other side of a VPN. A settings row that hangs for thirty seconds is worse than one that says
"nothing answered at this URL" in two. The split exists because those two waits have nothing in
common on a machine with no Ollama: see :data:`LOCAL_CONNECT_TIMEOUT_S`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from chimera.telemetry import get_logger

_log = get_logger("providers.ollama")

#: Seconds to wait for the tag list. Long enough for a local daemon that has to page itself back in,
#: short enough that a URL pointing at a machine that is off does not freeze the settings row asking.
DEFAULT_TIMEOUT_S = 2.0

#: Seconds allowed to OPEN the connection when the URL names this machine.
#:
#: A port on this machine either has something listening on it or it does not, and the kernel knows
#: which without asking anyone. Measured on Windows: a loopback port that IS listening accepts in at
#: most 16 ms over 30 attempts, while a port with nothing behind it takes **2.04 s** to come back
#: refused — and `localhost` resolves to both `::1` and `127.0.0.1`, so httpx pays that twice, for
#: 4.2 s of waiting to learn something the first millisecond already settled.
#:
#: So the connect gets its own budget, fifteen times the slowest accept ever observed, and the read
#: keeps the full one — a daemon that is listening but slow to answer is a different situation and
#: still gets its time. Measured through this function, paired on one machine: **2,012 ms -> 530 ms**
#: for `localhost` and **2,010 ms -> 265 ms** for `127.0.0.1` (one stack, so the wait is paid once),
#: against **15 ms -> 14 ms** for a URL where something answers — same verdict in all three.
#:
#: Deliberately NOT applied to a remote URL. An Ollama across a VPN can legitimately need longer than
#: this to accept, and reporting it unreachable to save a fraction of a second would be a lie about
#: the user's machine — the exact failure the rest of this module exists to avoid.
LOCAL_CONNECT_TIMEOUT_S = 0.25

#: Why no list came back. ``""`` when one did — including when it was empty, which is an answer.
Reason = Literal["", "no_url", "unreachable", "http_error", "not_ollama"]


@dataclass(frozen=True)
class InstalledModels:
    """What the configured Ollama answered, or why it did not."""

    #: The URL that was asked, echoed back so the client names the address the user set rather than
    #: a default it assumed.
    base_url: str
    #: True only when the server answered and the answer parsed. NOT implied by ``models`` being
    #: empty — an Ollama with nothing pulled is reachable and has an empty list, and the two states
    #: have opposite remedies ("pull something" vs "start the server").
    reachable: bool
    #: Tags exactly as Ollama spells them (``llama3:latest``), sorted, ready to prefix with ``ollama/``.
    models: tuple[str, ...] = ()
    reason: Reason = ""


def _connect_budget(base_url: str, timeout_s: float) -> float:
    """How long to spend opening the connection, which is not how long to spend waiting for a reply.

    Loopback only. Everything else keeps the caller's budget, because a slow accept from a machine
    across a network is normal and calling it unreachable would invent a fact about that machine.
    """
    from urllib.parse import urlsplit

    try:
        host = (urlsplit(base_url).hostname or "").lower()
    except ValueError:  # a URL malformed enough that even splitting it fails
        return timeout_s
    on_this_machine = host == "localhost" or host == "::1" or host.startswith("127.")
    if not on_this_machine:
        return timeout_s
    # `min`, not the constant: a caller who asked for a 50ms probe must not be handed a connect
    # budget five times longer than the whole thing it asked for.
    return min(LOCAL_CONNECT_TIMEOUT_S, timeout_s)


def installed_models(base_url: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> InstalledModels:
    """Ask the Ollama at ``base_url`` what it has pulled. Never raises.

    Every failure — no URL, no server, a 500, a body that is not the shape we expect — comes back as
    ``reachable=False`` with a reason, because this is called to populate a form and an exception
    there would turn "your local model server is off" into a 500 that reads as a bug in Chimera.
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return InstalledModels("", False, reason="no_url")

    try:
        import httpx
    except ImportError:  # pragma: no cover — httpx is a hard dependency
        return InstalledModels(base, False, reason="unreachable")

    # Two bounds, because two different things can be slow and only one of them is interesting.
    #
    # The connect budget (see `LOCAL_CONNECT_TIMEOUT_S`) is what makes the common case fast: on a
    # machine with no Ollama, `localhost` resolves to both `::1` and `127.0.0.1` and neither refuses
    # promptly, so httpx tries them in turn and pays the connect budget twice.
    #
    # The deadline behind it bounds the WHOLE probe, and still earns its place: it is the only thing
    # covering a server that ACCEPTS and then never answers, which no per-operation timeout catches
    # if the socket keeps dribbling bytes. An abandoned probe keeps running on its daemon thread and
    # its answer is discarded — the same contract every other deadline here has, and harmless for a
    # GET that changes nothing.
    from chimera.concurrency import call_with_deadline

    budget = httpx.Timeout(timeout_s, connect=_connect_budget(base, timeout_s))
    try:
        response = call_with_deadline(
            lambda: httpx.get(f"{base}/api/tags", timeout=budget), timeout_s
        )
    except Exception as exc:  # noqa: BLE001 — an absent Ollama is a normal state, not a 500
        _log.debug("ollama tag list failed at %s: %s", base, exc)
        return InstalledModels(base, False, reason="unreachable")
    if response.status_code >= 400:
        _log.debug("ollama tag list at %s answered %s", base, response.status_code)
        return InstalledModels(base, False, reason="http_error")

    try:
        payload = response.json()
        entries = payload["models"]
    except Exception:  # noqa: BLE001 — a 200 from something that is not Ollama
        return InstalledModels(base, False, reason="not_ollama")
    if not isinstance(entries, list):
        return InstalledModels(base, False, reason="not_ollama")

    tags: set[str] = set()
    for entry in entries:
        # `name` rather than `model`: both are present and equal on current Ollama, but `name` is the
        # one its own CLI prints, so it is the one a user recognises from `ollama list`.
        if isinstance(entry, dict) and isinstance(name := entry.get("name"), str) and name.strip():
            tags.add(name.strip())
    return InstalledModels(base, True, models=tuple(sorted(tags)))
