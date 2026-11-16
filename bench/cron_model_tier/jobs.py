"""Frozen jobs for the cron model-tier bench (P1-03 acceptance).

Each job is a prompt + a FROZEN synthetic source document + a fixed report template. The gabarito is
computed from the source by code here, never typed by hand, so it cannot disagree with the data.
Nothing in this file is trading data (house rule: numeric trading data is a deterministic script).

SYNTHETIC: the sources are invented but shaped like the PassaPro/ops reports the cron runs (metrics
integers, a few percentages, money). The result is valid for these 8 jobs, not for the 21 in
production.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Value kinds: "int" exact; "dec1" a percentage/decimal with 1 place (|diff| <= 0.05);
# "money" 2 places (|diff| <= 0.01); "set" a comma-separated set of names; "str" exact token.


@dataclass(frozen=True)
class Job:
    name: str
    prompt: str
    source: Any
    template: list[tuple[str, str]]  # (label, kind)
    expected: dict[str, Any]
    nothing_new: bool = False  # the right answer is exactly NOTHING_NEW


def _pct(a: float, b: float) -> float:
    return round(100.0 * a / b, 1)


def _activation() -> Job:
    days = [("2026-11-02", 41, 17), ("2026-11-03", 38, 15), ("2026-11-04", 55, 24), ("2026-11-05", 47, 19),
            ("2026-11-06", 62, 29), ("2026-11-07", 29, 10), ("2026-11-08", 33, 12)]
    src = {"days": [{"date": d, "signups": s, "activated": a} for d, s, a in days], "previous_week_signups": 281}
    s = sum(x[1] for x in days)
    a = sum(x[2] for x in days)
    best = max(days, key=lambda x: x[1])[0]
    return Job(
        "activation_weekly",
        "Weekly activation report for the study-prep app. Use the metrics file below.",
        src,
        [("Total signups", "int"), ("Total activated", "int"), ("Activation rate %", "dec1"),
         ("Signups change vs previous week", "int"), ("Best signup day", "str")],
        {"Total signups": s, "Total activated": a, "Activation rate %": _pct(a, s),
         "Signups change vs previous week": s - 281, "Best signup day": best},
    )


def _churn() -> Job:
    subs = [(1001, "pro", 29.9, "active"), (1002, "pro", 29.9, "canceled"), (1003, "basic", 14.9, "active"),
            (1004, "pro", 29.9, "active"), (1005, "basic", 14.9, "canceled"), (1006, "pro", 29.9, "active"),
            (1007, "basic", 14.9, "active"), (1008, "pro", 29.9, "canceled"), (1009, "basic", 14.9, "active"),
            (1010, "pro", 29.9, "active"), (1011, "basic", 14.9, "active"), (1012, "pro", 29.9, "active")]
    src = {"subscriptions": [{"id": i, "plan": p, "mrr_usd": m, "status": st} for i, p, m, st in subs]}
    canc = [x for x in subs if x[3] == "canceled"]
    lost = round(sum(x[2] for x in canc), 2)
    kept = round(sum(x[2] for x in subs if x[3] == "active"), 2)
    return Job(
        "churn_report",
        "Churn report: how many subscriptions were canceled and what it cost in MRR.",
        src,
        [("Canceled subscriptions", "int"), ("MRR lost (USD)", "money"), ("MRR remaining (USD)", "money"),
         ("Churn rate %", "dec1")],
        {"Canceled subscriptions": len(canc), "MRR lost (USD)": lost, "MRR remaining (USD)": kept,
         "Churn rate %": _pct(len(canc), len(subs))},
    )


def _triage() -> Job:
    t = [(4401, "high", 70, "billing"), (4402, "low", 5, "how-to"), (4403, "medium", 52, "bug"),
         (4404, "high", 12, "bug"), (4405, "low", 130, "how-to"), (4406, "medium", 8, "billing"),
         (4407, "high", 49, "bug"), (4408, "low", 20, "other"), (4409, "medium", 3, "bug"),
         (4410, "high", 26, "billing"), (4411, "low", 61, "other"), (4412, "medium", 47, "how-to"),
         (4413, "low", 1, "how-to"), (4414, "high", 90, "bug"), (4415, "medium", 15, "billing")]
    src = {"open_tickets": [{"id": i, "priority": p, "age_hours": a, "category": c} for i, p, a, c in t]}
    oldest = max(t, key=lambda x: x[2])
    return Job(
        "support_triage",
        "Support queue triage summary for the owner.",
        src,
        [("High priority tickets", "int"), ("Tickets older than 48h", "int"),
         ("Oldest ticket id", "str"), ("Oldest ticket age (h)", "int")],
        {"High priority tickets": sum(1 for x in t if x[1] == "high"),
         "Tickets older than 48h": sum(1 for x in t if x[2] > 48),
         "Oldest ticket id": str(oldest[0]), "Oldest ticket age (h)": oldest[2]},
    )


_SERVICES_BAD = [("web", 312, 0.4), ("api", 905, 1.1), ("worker", 410, 2.6), ("db", 221, 0.1),
                 ("search", 760, 0.9), ("mailer", 1180, 3.4)]
_SERVICES_OK = [("web", 312, 0.4), ("api", 640, 1.1), ("worker", 410, 0.8), ("db", 221, 0.1),
                ("search", 760, 0.9), ("mailer", 790, 1.9)]


def _health(name: str, rows: list[tuple[str, int, float]], quiet: bool) -> Job:
    src = {"services": [{"name": n, "p95_ms": p, "error_rate_pct": e} for n, p, e in rows],
           "rule": "A service is unhealthy when p95_ms > 800 OR error_rate_pct > 2.0."}
    bad = [r for r in rows if r[1] > 800 or r[2] > 2.0]
    return Job(
        name,
        "Service health check. Report only if something is unhealthy.",
        src,
        [("Unhealthy services", "set"), ("Unhealthy count", "int"), ("Worst p95 (ms)", "int")],
        {} if quiet else {"Unhealthy services": {r[0] for r in bad}, "Unhealthy count": len(bad),
                          "Worst p95 (ms)": max(r[1] for r in rows)},
        nothing_new=quiet,
    )


def _newsletter() -> Job:
    src = {"campaign": "november-digest", "sent": 1840, "bounced": 37, "opened": 702, "clicked": 96,
           "unsubscribed": 12, "note": "delivered = sent - bounced; open rate is over delivered; "
                                        "click-to-open rate = clicked / opened; bounce rate is over sent"}
    dl = 1840 - 37
    return Job(
        "newsletter_metrics",
        "Newsletter campaign report.",
        src,
        [("Delivered", "int"), ("Open rate %", "dec1"), ("Click-to-open rate %", "dec1"),
         ("Bounce rate %", "dec1")],
        {"Delivered": dl, "Open rate %": _pct(702, dl), "Click-to-open rate %": _pct(96, 702),
         "Bounce rate %": _pct(37, 1840)},
    )


def _reactivation() -> Job:
    days = [12, 34, 45, 61, 90, 7, 33, 120, 75, 29, 58, 101, 66, 31, 88, 14, 40, 95, 52, 27]
    users = [{"user": f"u{i + 1:02d}", "days_since_last_login": d, "plan": ("pro" if i % 3 == 0 else "free")}
             for i, d in enumerate(days)]
    return Job(
        "reactivation_segments",
        "Reactivation segments: bucket the inactive users for the win-back email.",
        {"inactive_users": users},
        [("30-59 days", "int"), ("60-89 days", "int"), ("90+ days", "int"), ("Pro users inactive 30+ days", "int")],
        {"30-59 days": sum(1 for d in days if 30 <= d <= 59), "60-89 days": sum(1 for d in days if 60 <= d <= 89),
         "90+ days": sum(1 for d in days if d >= 90),
         "Pro users inactive 30+ days": sum(1 for u in users if u["plan"] == "pro" and u["days_since_last_login"] >= 30)},
    )


def _cost() -> Job:
    d = [3.12, 2.87, 4.05, 3.66, 9.41, 2.95, 3.38]
    src = {"daily_llm_cost_usd": [{"date": f"2026-11-{2 + i:02d}", "usd": v} for i, v in enumerate(d)],
           "previous_week_total_usd": 21.10}
    tot = round(sum(d), 2)
    mx = max(range(7), key=lambda i: d[i])
    return Job(
        "cost_digest",
        "Weekly LLM spend digest.",
        src,
        [("Week total (USD)", "money"), ("Daily mean (USD)", "money"), ("Most expensive day", "str"),
         ("Change vs previous week %", "dec1")],
        {"Week total (USD)": tot, "Daily mean (USD)": round(tot / 7, 2), "Most expensive day": f"2026-11-{2 + mx:02d}",
         "Change vs previous week %": round(100.0 * (tot - 21.10) / 21.10, 1)},
    )


def all_jobs() -> list[Job]:
    return [_activation(), _churn(), _triage(), _health("health_alert", _SERVICES_BAD, False),
            _health("health_quiet", _SERVICES_OK, True), _newsletter(), _reactivation(), _cost()]


def user_message(job: Job) -> str:
    src = f"Source file (frozen):\n{json.dumps(job.source, indent=1)}"
    lines = "\n".join(f"{label}: <value>" for label, _ in job.template)
    if job.nothing_new:
        return (f"{job.prompt}\n\nIf nothing is unhealthy, reply with exactly NOTHING_NEW and nothing else. "
                "Otherwise write the report using EXACTLY this template (labels verbatim, one value per "
                f"line; the services as a comma-separated list):\n{lines}\n\n{src}")
    return (f"{job.prompt}\n\nWrite the report as a short chat message. Put each figure on its own line using "
            f"EXACTLY this template (labels verbatim, one value per line, numbers without units):\n{lines}\n"
            f"You may add one sentence of commentary after the template.\n\n{src}")
