"""Usage log: one append-only JSONL record per chat turn, aggregated for the Cost / Usage dashboard.

Mirrors :mod:`chimera.fusion.route_log` (Pydantic ``.model_dump_json()`` per line, malformed lines
skipped on load). The honesty rule lives in :func:`summarize_usage`: a turn whose ``usd`` price is
unknown is None, never 0 — the summary SUMS only the present prices and COUNTS the unpriced turns
separately, so an unknown price can never be laundered into a fake $0.
"""

from __future__ import annotations

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
    route_kind: str | None = None  # "fusion" | "cascade" | None (single-model turn)


def append_usage(path: Path, record: UsageRecord) -> None:
    """Append one usage record as a JSON line."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json() + "\n")


def load_usage(path: Path) -> list[UsageRecord]:
    """Load persisted usage records; malformed lines are skipped."""
    path = Path(path)
    if not path.exists():
        return []
    out: list[UsageRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(UsageRecord.model_validate_json(line))
        except ValueError:  # pragma: no cover - defensive
            _log.warning("skipping malformed usage record line")
    return out


def summarize_usage(records: list[UsageRecord]) -> dict[str, Any]:
    """Aggregate usage records into the dashboard summary.

    ``usd`` is summed from ONLY the records that carry a price; records with ``usd is None`` are
    counted as ``unpriced_turns`` and never added as 0 — so the total spend is honest about what it
    actually knows the cost of.
    """
    totals = {
        "turns": len(records),
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
        _accumulate(by_day, day, "day", day, r)
        _accumulate(by_model, r.model, "model", r.model, r)
        _accumulate(by_session, r.session_id, "session_id", r.session_id, r)

        kind = r.route_kind if r.route_kind in ("fusion", "cascade") else "single"
        route_mix[kind] += 1

    denom = totals["prompt_tokens"] + cache_read_total
    cache_hit_pct = (cache_read_total / denom) if denom else None

    return {
        "totals": totals,
        "by_day": sorted(by_day.values(), key=lambda d: d["day"]),
        "by_model": sorted(by_model.values(), key=lambda m: m["turns"], reverse=True),
        "by_session": sorted(by_session.values(), key=lambda s: s["turns"], reverse=True)[:20],
        "cache_hit_pct": cache_hit_pct,
        "route_mix": route_mix,
    }


def _accumulate(
    bucket: dict[str, dict[str, Any]], key: str, key_field: str, key_value: str, r: UsageRecord
) -> None:
    """Fold one record into a group (by day / model / session), summing usd only when it is present."""
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
    entry["turns"] += 1
    entry["prompt_tokens"] += r.prompt_tokens
    entry["completion_tokens"] += r.completion_tokens
    if r.usd is not None:
        entry["usd"] += r.usd
    else:
        entry["unpriced"] += 1
