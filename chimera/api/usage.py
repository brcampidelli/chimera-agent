"""Usage log: one append-only JSONL record per chat turn, aggregated for the Cost / Usage dashboard.

Mirrors :mod:`chimera.fusion.route_log` (Pydantic ``.model_dump_json()`` per line, malformed lines
skipped on load). The honesty rule lives in :func:`summarize_usage`: a turn whose ``usd`` price is
unknown is None, never 0 — the summary SUMS only the present prices and COUNTS the unpriced turns
separately, so an unknown price can never be laundered into a fake $0.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from chimera.telemetry import get_logger

_log = get_logger("api.usage")


class UsageRecord(BaseModel):
    """One chat turn's token/cost accounting, as persisted to the usage log."""

    ts: str = ""  # ISO-8601 UTC timestamp of the turn
    session_id: str = ""
    model: str = ""  # the model slug that answered ("" when the backend didn't report one)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    usd: float | None = None  # list-rate cost, or None when the model's price is unknown — never guessed
    tools: int = 0
    memory_facts: int = 0
    #: "fusion" | "cascade" | "hierarchy" | :data:`MEMORY_KIND` | :data:`TIDY_KIND` | None
    #: (single-model turn).
    route_kind: str | None = None
    #: How many of this turn's tool calls a gate refused or that failed outright.
    #:
    #: The census counted `tools` — calls attempted — which is the same number whether the work
    #: happened or a gate stopped it. A turn that paid for a model, called `run_shell` three times
    #: and ran nothing reads identically to one that ran three commands, and the difference is the
    #: only interesting thing about it. Optional with a default, so records written before this
    #: field existed still load.
    declined: int = 0


#: The ``route_kind`` of a row that prices a call made FOR a turn rather than a turn itself: the
#: memory extraction after it (`chimera.memory.extract`). Filed under the turn's own session id,
#: so the conversation's spend includes it, and not counted as a turn, because it is not one: with
#: extraction on, every turn would otherwise read as two on the Cost screen.
MEMORY_KIND = "memory"

#: The ``route_kind`` of a row that prices "Tidy memory"'s merge when it runs after a terminal
#: conversation rather than inside a turn (`chimera chat`, `chimera assist`; on the Code screen the
#: merge is inside the turn and on the turn's own row). Filed under that conversation's id and not
#: counted as a turn, for the reason :data:`MEMORY_KIND` is not. A kind of its own, so the
#: extraction's rows and the tidy's can still be told apart.
TIDY_KIND = "memory_tidy"

#: The route kinds of rows that price a call made FOR a conversation, not a turn of it.
NOT_A_TURN: frozenset[str] = frozenset({MEMORY_KIND, TIDY_KIND})


def append_usage(path: Path, record: UsageRecord) -> None:
    """Append one usage record as a JSON line."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json() + "\n")


def record_turn(home: Path, session_id: str, report: Any) -> None:
    """Append one chat turn's accounting to the usage log, from a ``TurnReport``.

    ``report`` is duck-typed so a caller outside the API package does not have to import the API's
    models to write a row. This is the one implementation: ``chimera.api.app._append_usage``
    delegates here, and so does the terminal.

    Best-effort in the same way every other logging call here is — see :func:`record_spend`. The
    terminal wrote NOTHING to this file until now, on any of its three surfaces, so the Cost screen
    and every self-measurement this project runs excluded the surface it is used from most.
    """
    from datetime import UTC, datetime

    try:
        route_meta = getattr(report, "route_meta", None)
        append_usage(
            Path(home) / "usage.jsonl",
            UsageRecord(
                ts=datetime.now(UTC).isoformat(),
                session_id=session_id,
                model=getattr(report, "model", "") or "",
                prompt_tokens=getattr(report, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(report, "completion_tokens", 0) or 0,
                cache_read_tokens=getattr(report, "cache_read_tokens", 0) or 0,
                cache_write_tokens=getattr(report, "cache_write_tokens", 0) or 0,
                usd=getattr(report, "usd", None),
                tools=len(getattr(report, "tool_names", None) or []),
                memory_facts=getattr(report, "memory_facts_used", 0) or 0,
                route_kind=route_meta.get("kind") if isinstance(route_meta, dict) else None,
                declined=len(getattr(report, "declined", None) or []),
            ),
        )
    except Exception as exc:  # noqa: BLE001 — see the docstring
        _log.debug("usage logging skipped: %s", exc)


def record_spend(
    home: Path,
    *,
    session_id: str,
    model: str = "",
    prompt_tokens: int | None = 0,
    completion_tokens: int | None = 0,
    cache_read_tokens: int | None = 0,
    cache_write_tokens: int | None = 0,
    usd: float | None = None,
    tools: int = 0,
    route_kind: str | None = None,
) -> None:
    """Log one spending call from anywhere, best-effort.

    The Cost screen reads this file and reports what it finds as *the* spend. It was written by two
    paths out of the many that call models — and one of those two, ``/api/chat/stream``, is a route
    no screen in the app calls. So the dashboard reported one path's spending as the whole bill,
    which is worse than an absent dashboard: an absent one is not consulted, and this one answered
    a question with a number that was confidently too low.

    Best-effort in the same way every other logging call here is: failing to record what a run cost
    must never be the reason the run fails. The answer is the product.
    """
    from datetime import UTC, datetime

    try:
        append_usage(
            Path(home) / "usage.jsonl",
            UsageRecord(
                ts=datetime.now(UTC).isoformat(),
                session_id=session_id,
                model=model or "",
                prompt_tokens=prompt_tokens or 0,
                completion_tokens=completion_tokens or 0,
                cache_read_tokens=cache_read_tokens or 0,
                cache_write_tokens=cache_write_tokens or 0,
                usd=usd,
                tools=tools,
                route_kind=route_kind,
            ),
        )
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        _log.debug("usage logging skipped: %s", exc)


#: Marks a record reconstructed from a run receipt. Chat writes a bare session id and the
#: orchestrator writes ``orchestration:``, so the three namespaces cannot collide — which is what
#: keeps the merge below from counting the same money twice without needing to check.
RUN_SESSION_PREFIX = "run:"


def _already_counted(records: list[UsageRecord]) -> set[str]:
    """Run ids that ``usage.jsonl`` ALREADY accounts for, so the merge cannot bill them twice.

    The scheduled path writes both: a usage row keyed ``cron:<job>:<run_id>`` *and* a run receipt
    carrying the same ``run_id``. Measured on a real install — four nightly runs, ~$0.049, counted
    once in each file and therefore twice on the Cost screen.

    The first version of this merge argued that the id NAMESPACES cannot collide, which is true and
    was the wrong property: the collision is not between two ids, it is the same WORK written to
    two files under different names. Only the run id joins them, so only the run id can separate
    them.

    Read from compound ids alone (``a:b:<run_id>``). A bare session id is a chat turn, which writes
    no receipt — treating one as a run id could silently drop a real charge, and under-counting
    money is the direction that must never happen by accident.
    """
    seen: set[str] = set()
    for r in records:
        partes = str(r.session_id or "").split(":")
        if len(partes) >= 2 and partes[-1]:
            seen.add(partes[-1])
    return seen


def usage_from_runs(path: Path, *, already: set[str] | None = None) -> list[UsageRecord]:
    """One record per ATTEMPT of every autonomous run, so the Cost screen counts them too.

    ``record_spend`` has exactly two callers — a chat turn and an orchestration run — and the
    autonomous run path is not one of them. Runs write their receipts to ``runs.jsonl`` instead,
    which the Cost screen never read: measured in the desktop app, a day with $0.0270 of runs
    showed a total that stopped at the previous day. The screen answered "how much have I spent"
    with a number that was confidently too low, and too low by exactly the most expensive kind of
    work the app does.

    Read here rather than ALSO written there: two writers of one number drift, and the receipt is
    already the record. ``usd`` is the attempt's own total — ``overhead_usd`` is a share of it, not
    an addition — and stays None when the price is unknown, which `summarize_usage` counts as an
    unpriced turn rather than laundering into $0.
    """
    path = Path(path)
    if not path.exists():
        return []
    contadas = already or set()
    out: list[UsageRecord] = []
    # Streamed for the reason spelled out in `chimera.api.runs.load_runs`: the whole-file read
    # peaks at four times the file, and this is the same file.
    with path.open(encoding="utf-8", errors="replace") as arquivo:
        for line in arquivo:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                _log.warning("skipping malformed run record line")
                continue
            if not isinstance(row, dict):
                continue
            run_ts = str(row.get("ts") or "")
            tentativas = [a for a in (row.get("attempts") or []) if isinstance(a, dict)]
            # The WHOLE run, not the one attempt whose id matched. A scheduled dispatch writes ONE
            # usage row holding the total across every attempt, keyed by the LAST attempt's id — so
            # skipping only that attempt left the earlier ones to be added on top of a total that
            # already contained them. Measured on a real job: two attempts of $0.004247 and $0.006598
            # against a $0.010845 row, counted as $0.015092. The first version of this join fixed half
            # the double-count and the arithmetic said so.
            if any(str(a.get("run_id") or "") in contadas for a in tentativas):
                continue
            for attempt in tentativas:
                usd = attempt.get("usd")
                out.append(
                    UsageRecord(
                        ts=run_ts,
                        # The attempt carries the run id; the run row itself does not.
                        session_id=RUN_SESSION_PREFIX + str(attempt.get("run_id") or run_ts),
                        model=str(attempt.get("model") or ""),
                        prompt_tokens=int(attempt.get("prompt_tokens") or 0),
                        completion_tokens=int(attempt.get("completion_tokens") or 0),
                        usd=float(usd) if isinstance(usd, int | float) else None,
                        tools=len(attempt.get("tool_names") or []),
                    )
                )
    return out


def load_usage(path: Path) -> list[UsageRecord]:
    """Load persisted usage records; malformed lines are skipped."""
    path = Path(path)
    if not path.exists():
        return []
    out: list[UsageRecord] = []
    # Streamed for the same reason as the run log above.
    with path.open(encoding="utf-8", errors="replace") as arquivo:
        for line in arquivo:
            if not line.strip():
                continue
            try:
                out.append(UsageRecord.model_validate_json(line))
            except ValueError:  # pragma: no cover - defensive
                _log.warning("skipping malformed usage record line")
    return out


def spent_today(path: Path, *, today: str) -> tuple[float, bool]:
    """What has been spent on ``today`` (an ISO ``YYYY-MM-DD``), and whether any of it is unknown.

    Returns ``(usd, has_unpriced)``. The second value is not a detail: a day containing one unpriced
    turn has an unknown total, and a cap compared against the known part alone would be comparing
    against a number that is confidently too low. The caller decides what to do with that — the
    scheduler refuses, which is the same rule the per-run cap follows.

    Reads the log rather than keeping a counter, so the answer survives a restart. The daemon runs
    for weeks; a total held in memory would reset to zero every deploy, which is the one moment a
    spend cap most needs to remember.
    """
    total = 0.0
    unpriced = False
    for record in load_usage(path):
        if not record.ts.startswith(today):
            continue
        if record.usd is None:
            unpriced = True
        else:
            total += record.usd
    return round(total, 6), unpriced


def summarize_usage(records: list[UsageRecord]) -> dict[str, Any]:
    """Aggregate usage records into the dashboard summary.

    ``usd`` is summed from ONLY the records that carry a price; records with ``usd is None`` are
    counted as ``unpriced_turns`` and never added as 0 — so the total spend is honest about what it
    actually knows the cost of.

    A :data:`MEMORY_KIND` or :data:`TIDY_KIND` row (:data:`NOT_A_TURN`) adds its tokens and price
    everywhere, and its unknown price to the unpriced count, but it is not a turn and is not
    counted as one, in the totals or in any group. An unknown extraction price can therefore turn a
    group's price into "unknown" on the screen, which is the direction this file prefers to a total
    that is confidently too low.
    """
    totals = {
        "turns": sum(1 for r in records if r.route_kind not in NOT_A_TURN),
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "usd": 0.0,
        "unpriced_turns": 0,
    }
    by_day: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    by_session: dict[str, dict[str, Any]] = {}
    route_mix = {"single": 0, "fusion": 0, "cascade": 0}
    cache_read_total = 0

    for r in records:
        totals["prompt_tokens"] += r.prompt_tokens
        totals["completion_tokens"] += r.completion_tokens
        totals["cache_read_tokens"] += r.cache_read_tokens
        totals["cache_write_tokens"] += r.cache_write_tokens
        cache_read_total += r.cache_read_tokens
        if r.usd is None:
            totals["unpriced_turns"] += 1
        else:
            totals["usd"] += r.usd

        day = r.ts[:10]  # "YYYY-MM-DD" prefix of the ISO timestamp
        turn = r.route_kind not in NOT_A_TURN
        _accumulate(by_day, day, "day", day, r, count=turn)
        # Per model a row is a call that model answered, whatever it was for. Counting only turns
        # here would leave the default model, when it answers nothing but extractions, a group of
        # zero turns, which the Cost screen reads as a group whose price is unknown.
        _accumulate(by_model, r.model, "model", r.model, r, count=True)
        _accumulate(by_session, r.session_id, "session_id", r.session_id, r, count=turn)

        if r.route_kind in NOT_A_TURN:
            continue  # a call made for a turn: its route is the turn's, already counted
        kind = r.route_kind if r.route_kind in ("fusion", "cascade") else "single"
        route_mix[kind] += 1

    denom = totals["prompt_tokens"] + cache_read_total
    cache_hit_pct = (cache_read_total / denom) if denom else None

    return {
        "totals": totals,
        "by_day": sorted(by_day.values(), key=lambda d: d["day"]),
        "by_model": sorted(by_model.values(), key=lambda m: m["turns"], reverse=True),
        # Ties broken by SPEND, not by input order. Nearly every session is one turn, so the
        # old key left the order to whichever log was concatenated first — and runs are
        # appended last, which would have kept the most expensive work out of the panel
        # that exists to show where the money went.
        "by_session": sorted(
            by_session.values(), key=lambda s: (s["turns"], s["usd"]), reverse=True
        )[:20],
        "cache_hit_pct": cache_hit_pct,
        "route_mix": route_mix,
    }


def _accumulate(
    bucket: dict[str, dict[str, Any]],
    key: str,
    key_field: str,
    key_value: str,
    r: UsageRecord,
    *,
    count: bool = True,
) -> None:
    """Fold one record into a group (by day / model / session), summing usd only when it is present.

    ``count`` says whether the record adds to the group's ``turns``; its tokens and price are added
    either way.
    """
    entry = bucket.get(key)
    if entry is None:
        entry = {
            key_field: key_value,
            "turns": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "usd": 0.0,
            "unpriced": 0,  # turns in this group whose price is unknown (usd is None)
        }
        bucket[key] = entry
    if count:
        entry["turns"] += 1
    entry["prompt_tokens"] += r.prompt_tokens
    entry["completion_tokens"] += r.completion_tokens
    if r.usd is not None:
        entry["usd"] += r.usd
    else:
        entry["unpriced"] += 1
