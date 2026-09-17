"""The route a streamed call could not name, learned from the router's generation record.

A receipt says which route served a call because the score, the cost and the cache all belong to
the route rather than to the model id (`bench/cache_confound`). On the non-streamed path the router
puts ``provider`` on the response and the receipt has it at once. On the streamed path — the one the
Code screen uses — it never arrives: measured on 2026-09-16 with `litellm` against OpenRouter, a
chunk exposes no provider anywhere (not on the chunk, not in ``_hidden_params``, not in
``provider_specific_fields``), while the same request without streaming answers ``provider``.

What the chunk does carry is its ``id`` (``gen-…``), and the router keeps a record per generation:
``GET https://openrouter.ai/api/v1/generation?id=<id>`` answers ``provider_name`` — and
``native_tokens_cached``, ``total_cost``, ``streamed``. **~9–11 seconds after the stream ends.**
Measured, three streamed calls: 404 for the first ~9 s, then the record, and three different routes
for three identical requests (`Mancer 2`, `OpenInference`, `Together`). So nothing on the request
path can wait for it — a lookup per step would add ten seconds to every step of every turn. The id
is recorded on the receipt when the turn ends (`StepLog.generation_ids`), and the route is filled in
later, where the receipt is read back: the conversation's replay.

Three rules keep the fill-in honest. **One request, no retry, bounded:** the replay of a long
conversation must not become a tour of the router, so a lookup times out fast and the whole pass
has a wall-clock budget; what did not resolve stays ``""`` and is tried on the next reopen. **The
first id, not all of them:** ``StepLog.provider`` names the route the trajectory was set by, and
this fills the same field with the same meaning; the per-step ids stay on the receipt for a reader
who wants the rest. **Never guessed:** a 404, a timeout or a body without a name leave the field
empty — the three-state rule every receipt field here follows.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("providers.generation")

#: Where the router keeps one record per generation. Only OpenRouter has this; the receipt's
#: ``model`` says whether a call went through it.
GENERATION_URL = "https://openrouter.ai/api/v1/generation"

#: One lookup's own bound. The record is either there or not; waiting longer buys nothing.
LOOKUP_TIMEOUT = 2.0

#: The whole pass's bound, so reopening a conversation with forty unresolved turns costs at most
#: this and not forty round trips.
PASS_BUDGET = 2.5

Opener = Callable[..., Any]


def lookup_route(
    generation_id: str, *, api_key: str, timeout: float = LOOKUP_TIMEOUT, opener: Opener = urllib.request.urlopen
) -> str:
    """The route that served ``generation_id``, or ``""`` when the router does not (yet) say.

    ``opener`` is the seam a test hands a fake to; production is ``urllib``. No retry here on
    purpose — the caller decides how many ids it can afford, and a record that is not there yet
    will be there at the next reopen.
    """
    if not generation_id or not api_key:
        return ""
    req = urllib.request.Request(
        f"{GENERATION_URL}?{urllib.parse.urlencode({'id': generation_id})}",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with opener(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 404 is the ordinary "not yet" for the first ~10 s; anything else is logged at the same
        # level because the receipt reads the same either way — empty, not wrong.
        _log.debug("generation %s: HTTP %s", generation_id, exc.code)
        return ""
    except (OSError, ValueError) as exc:
        _log.debug("generation %s: %s", generation_id, exc)
        return ""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return ""
    return str(data.get("provider_name") or "")


def wants_route(receipt: dict[str, Any]) -> bool:
    """A receipt that has ids to ask about, went through the router, and does not know its route."""
    return (
        not receipt.get("provider")
        and bool(receipt.get("generation_ids"))
        and str(receipt.get("model") or "").startswith("openrouter/")
    )


def resolve_missing_routes(
    receipts: list[dict[str, Any]],
    *,
    api_key: str,
    budget_seconds: float = PASS_BUDGET,
    lookup: Callable[..., str] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Fill ``provider`` on every receipt that can learn it, newest first, within the budget.

    Returns how many were filled. Newest first because that is the turn the person just had and
    the one drawn at the bottom of the screen; the budget is what keeps a long conversation's
    replay from waiting on the router for every turn it ever had.
    """
    if not api_key:
        return 0
    # Resolved at call time, not bound at definition: a test that patches `lookup_route` on this
    # module must reach the pass, and the production caller passes nothing.
    ask = lookup or lookup_route
    deadline = clock() + budget_seconds
    filled = 0
    for receipt in reversed(receipts):
        if not wants_route(receipt):
            continue
        if clock() >= deadline:
            break
        first = str(receipt["generation_ids"][0])
        route = ask(first, api_key=api_key, timeout=min(LOOKUP_TIMEOUT, max(0.1, deadline - clock())))
        if route:
            receipt["provider"] = route
            filled += 1
    return filled
