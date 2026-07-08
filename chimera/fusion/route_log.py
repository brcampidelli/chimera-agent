"""Route log (M16-A6): every cascade decision, persisted as future router training data.

RouteLLM's lesson: a learned pre-generation router needs preference data — which
tier was tried, which was accepted, what it cost. We LOG that data now (an
anti-scope decision: no router is trained in M16); the cascade's decisions become
the dataset that could later replace the wasted cheap call on hard turns.

Privacy: the prompt is stored as a sha256 hash + length only — no prompt text
ever enters telemetry.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from pydantic import BaseModel, Field

from chimera.telemetry import get_logger

_log = get_logger("fusion.route_log")


class RouteRecord(BaseModel):
    """One routed turn: what was tried, what was accepted, what it cost."""

    ts: float = Field(default_factory=time.time)
    prompt_chars: int = 0
    prompt_sha: str = ""
    """sha256 of the prompt — hash, not text; no prompt leakage into telemetry."""
    fuse_reason: str = "none"
    """RoutingPolicy attribution (mode/length/keyword/precision/arithmetic/none)."""
    tiers_tried: list[str] = Field(default_factory=list)
    accepted_tier: str = ""
    models: dict[str, str] = Field(default_factory=dict)
    """tier -> model slug actually used at that tier."""
    tokens_by_tier: dict[str, int] = Field(default_factory=dict)
    """tier -> total tokens spent at that tier (0 when the provider reported none)."""
    agreement: float | None = None
    """Weak-tier k-sample agreement score, when sampled (None otherwise)."""


def prompt_fingerprint(text: str) -> tuple[int, str]:
    """(chars, sha256) for a prompt — the only two things the log keeps about it."""
    return len(text), hashlib.sha256(text.encode("utf-8")).hexdigest()


def append_route(path: Path, record: RouteRecord) -> None:
    """Append one route record as a JSON line."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json() + "\n")


def load_routes(path: Path) -> list[RouteRecord]:
    """Load persisted route records; malformed lines are skipped."""
    path = Path(path)
    if not path.exists():
        return []
    out: list[RouteRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(RouteRecord.model_validate_json(line))
        except ValueError:  # pragma: no cover - defensive
            _log.warning("skipping malformed route record line")
    return out


def summarize_routes(records: list[RouteRecord]) -> dict[str, object]:
    """Tier distribution, escalation rate, and tokens by tier — the session receipt."""
    n = len(records)
    if n == 0:
        return {"n": 0}
    accepted: dict[str, int] = {}
    tokens: dict[str, int] = {}
    escalations = 0
    for r in records:
        accepted[r.accepted_tier] = accepted.get(r.accepted_tier, 0) + 1
        for tier, spent in r.tokens_by_tier.items():
            tokens[tier] = tokens.get(tier, 0) + spent
        if len(r.tiers_tried) > 1:
            escalations += 1
    return {
        "n": n,
        "accepted_by_tier": accepted,
        "escalation_rate": round(escalations / n, 4),
        "tokens_by_tier": tokens,
        "total_tokens": sum(tokens.values()),
    }


def format_route_summary(summary: dict[str, object]) -> str:
    """Compact CLI rendering — the per-session 'cheap by default' receipt."""
    if not summary.get("n"):
        return "no routed turns yet"
    accepted = dict(summary.get("accepted_by_tier", {}))  # type: ignore[call-overload]
    tokens = dict(summary.get("tokens_by_tier", {}))  # type: ignore[call-overload]
    dist = ", ".join(f"{k}={v}" for k, v in sorted(accepted.items()))
    spent = ", ".join(f"{k}={v}" for k, v in sorted(tokens.items()))
    rate_obj = summary.get("escalation_rate", 0.0)
    rate = rate_obj if isinstance(rate_obj, int | float) else 0.0
    return (
        f"turns: {summary['n']}  accepted by tier: {dist}\n"
        f"escalated past first tier: {rate:.0%}\n"
        f"tokens by tier: {spent or 'none reported'}  total: {summary.get('total_tokens', 0)}"
    )


def _dump(record: RouteRecord) -> str:  # pragma: no cover - debugging helper
    return json.dumps(record.model_dump(), indent=2)
