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

**The timeout is short and deliberate.** This is asked from a settings panel, and the configured URL
may point at a machine that is off, asleep, or on the other side of a VPN. A settings row that hangs
for thirty seconds is worse than one that says "nothing answered at this URL" in two.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from chimera.telemetry import get_logger

_log = get_logger("providers.ollama")

#: Seconds to wait for the tag list. Long enough for a local daemon that has to page itself back in,
#: short enough that a URL pointing at a machine that is off does not freeze the settings row asking.
DEFAULT_TIMEOUT_S = 2.0

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

    # The deadline bounds the WHOLE probe, not each connection attempt.
    #
    # `timeout_s` is httpx's per-operation budget, and `localhost` resolves to both `::1` and
    # `127.0.0.1`. When nothing is listening, neither refuses on every stack — so httpx tries them
    # in turn and pays the full timeout each. Measured live: 4.4s against the 2.0s this module's
    # own docstring promises, on every machine WITHOUT Ollama, which is most of them. And it is
    # paid by the model picker as a whole, because listing calls this.
    #
    # An abandoned probe keeps running on its daemon thread and its answer is discarded — the same
    # contract every other deadline here has, and harmless for a GET that changes nothing.
    from chimera.concurrency import call_with_deadline

    try:
        response = call_with_deadline(
            lambda: httpx.get(f"{base}/api/tags", timeout=timeout_s), timeout_s
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
