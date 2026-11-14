"""The Cost screen, the weekly review and the daily cap read ONE ledger (P0-10, a04-03).

The cap read ``usage.jsonl`` alone while the screen also summed ``runs.jsonl``, so a ``chimera
solve`` that cost money the same day showed on the screen and did not slow the cron. The test is
the one the audit asked for: build a home where the two logs hold different money, and require the
three readers to agree to the cent.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chimera.api.usage import UsageRecord, append_usage, ledger_rows, spent_today
from chimera.config import Settings

TODAY = datetime.now(UTC).strftime("%Y-%m-%d")


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    # A chat turn: usage.jsonl only.
    append_usage(
        home / "usage.jsonl",
        UsageRecord(ts=f"{TODAY}T09:00:00+00:00", session_id="chat1", model="m", usd=0.25),
    )
    # A scheduled run: a usage row AND a receipt under the same run id — must be billed once.
    append_usage(
        home / "usage.jsonl",
        UsageRecord(ts=f"{TODAY}T10:00:00+00:00", session_id="cron:job:r-cron", model="m", usd=0.10),
    )
    receipts = [
        {"ts": f"{TODAY}T10:00:00+00:00", "attempts": [{"run_id": "r-cron", "usd": 0.10, "model": "m"}]},
        # A `solve`: receipt only. This is the money the cap could not see.
        {"ts": f"{TODAY}T11:00:00+00:00", "attempts": [{"run_id": "r-solve", "usd": 3.0, "model": "m"}]},
    ]
    (home / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in receipts), encoding="utf-8")
    return home


def test_a_solve_of_the_day_shows_in_spent_today(tmp_path: Path) -> None:
    home = _home(tmp_path)

    spent, unpriced = spent_today(home / "usage.jsonl", today=TODAY)

    # 0.25 chat + 0.10 scheduled (once) + 3.00 solve. The old cap saw 0.35.
    assert (spent, unpriced) == (3.35, False)


def test_screen_and_cap_give_the_same_total_to_the_cent(tmp_path: Path) -> None:
    from chimera.api.app import build_api_app

    home = _home(tmp_path)
    client = TestClient(build_api_app(lambda: None, settings=Settings(CHIMERA_HOME=str(home))))  # type: ignore[arg-type]  # no backend needed

    screen = client.get("/api/usage").json()["totals"]["usd"]
    cap, _ = spent_today(home / "usage.jsonl", today=TODAY)
    ledger = round(sum(r.usd or 0.0 for r in ledger_rows(home)), 6)

    assert round(screen, 2) == round(cap, 2) == round(ledger, 2) == 3.35


def test_the_weekly_review_reads_the_same_ledger(tmp_path: Path) -> None:
    from chimera.scheduler.weekly_review import _spend, _Window

    home = _home(tmp_path)
    window = _Window(
        datetime(2000, 1, 1, tzinfo=UTC).timestamp(), datetime(2100, 1, 1, tzinfo=UTC).timestamp()
    )

    assert _spend(home, window).usd == 3.35


# --- the second review: id-less receipts, bad lines, cost of a check, day boundaries -------------


def _ts(offset: str = "+00:00", day: str = TODAY, clock: str = "12:00:00") -> str:
    return f"{day}T{clock}{offset}"


def _runs(home: Path, *rows: object) -> None:
    with (home / "runs.jsonl").open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def test_a_minimal_receipt_without_a_run_id_is_not_billed_twice(tmp_path: Path) -> None:
    """`_persist_minimal_receipt` wrote attempts with no run id, so the join by id could not see
    that the scheduler's usage row was the same money: 2 x $0.40."""
    home = tmp_path / "home"
    home.mkdir()
    append_usage(
        home / "usage.jsonl",
        UsageRecord(ts=_ts(), session_id="cron:j:r1", model="m", usd=0.40),
    )
    _runs(home, {"ts": _ts(clock="12:00:05"), "partial": True, "attempts": [{"usd": 0.40, "model": "m"}]})

    assert spent_today(home / "usage.jsonl", today=TODAY) == (0.4, False)
    assert round(sum(r.usd or 0 for r in ledger_rows(home)), 6) == 0.4


def test_an_id_less_receipt_with_a_different_total_is_still_counted(tmp_path: Path) -> None:
    # The match is on the money AND the moment, so a real second run is never swallowed.
    home = tmp_path / "home"
    home.mkdir()
    append_usage(home / "usage.jsonl", UsageRecord(ts=_ts(), session_id="cron:j:r1", usd=0.40))
    _runs(home, {"ts": _ts(clock="12:00:05"), "attempts": [{"usd": 0.07}]})

    assert spent_today(home / "usage.jsonl", today=TODAY) == (0.47, False)


def test_the_minimal_receipt_now_carries_the_run_id() -> None:
    import json as _json
    import tempfile
    from types import SimpleNamespace

    from chimera.core.autonomous import AutonomousAgent

    log = Path(tempfile.mkdtemp()) / "runs.jsonl"
    attempt = SimpleNamespace(index=1, success=True, prompt_tokens=5, completion_tokens=1, usd=0.1,
                              model="m", evaluator="none", run_id="r-9")
    me = SimpleNamespace(run_log=log, workspace=None, verifier=None, verify_source="")
    AutonomousAgent._persist_minimal_receipt(  # type: ignore[arg-type]  # a stand-in `self`
        me, SimpleNamespace(success=True, attempts=[attempt]), "t", RuntimeError("x")
    )

    assert _json.loads(log.read_text(encoding="utf-8"))["attempts"][0]["run_id"] == "r-9"


def test_a_malformed_historical_line_does_not_abort_the_cap_check(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    old = {"ts": "2026-01-02T10:00:00+00:00"}
    _runs(
        home,
        {**old, "attempts": 1},
        {**old, "attempts": [{"prompt_tokens": "inválido", "usd": 1.0}]},
    )

    # Not today: ignored, and not "unknown" either.
    assert spent_today(home / "usage.jsonl", today=TODAY) == (0.0, False)

    _runs(home, {"ts": _ts(), "attempts": 1})
    # Today: it cannot be read, so today's total is unknown — the cap refuses rather than guess.
    assert spent_today(home / "usage.jsonl", today=TODAY) == (0.0, True)


def test_a_check_does_not_parse_the_lines_of_other_days(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.api.usage as usage_mod

    home = tmp_path / "home"
    home.mkdir()
    with (home / "runs.jsonl").open("w", encoding="utf-8") as fh:
        for i in range(5000):
            fh.write(json.dumps({"ts": "2025-03-01T10:00:00+00:00", "attempts": [{"run_id": f"o{i}", "usd": 1.0}]}) + "\n")
        fh.write(json.dumps({"ts": _ts(), "attempts": [{"run_id": "today", "usd": 0.5}]}) + "\n")
    calls = {"n": 0}
    real = usage_mod.json.loads

    def counting(*a: object, **k: object) -> object:
        calls["n"] += 1
        return real(*a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(usage_mod.json, "loads", counting)
    started = time.monotonic()

    assert spent_today(home / "usage.jsonl", today=TODAY) == (0.5, False)
    assert calls["n"] == 1  # only today's line was deserialised
    assert time.monotonic() - started < 2.0


def test_the_day_is_the_utc_day_not_the_prefix_of_the_timestamp(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    # 23:30 on the 12th at UTC-4 is 03:30 on the 13th UTC.
    append_usage(
        home / "usage.jsonl",
        UsageRecord(ts="2026-11-12T23:30:00-04:00", session_id="chat", usd=2.0),
    )

    assert spent_today(home / "usage.jsonl", today="2026-11-13") == (2.0, False)
    assert spent_today(home / "usage.jsonl", today="2026-11-12") == (0.0, False)
    # ... and the same for a receipt in runs.jsonl.
    _runs(home, {"ts": "2026-11-13T01:00:00+14:00", "attempts": [{"run_id": "x", "usd": 3.0}]})
    assert spent_today(home / "usage.jsonl", today="2026-11-12") == (3.0, False)  # 11:00 UTC on the 12th
