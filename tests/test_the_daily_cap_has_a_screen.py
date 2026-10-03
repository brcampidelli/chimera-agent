"""The day's dollar ceiling can be read and set from the app (study 29, P2.1).

`CHIMERA_DAILY_USD_CAP` existed and braked the scheduler (`chimera/scheduler/job_runner.py`) with
no way in but `.env`: `PATCH /api/config` refused the key and `GET /api/config` did not report it.
These tests are about that wiring and about the values a screen can send — the brake itself is
covered where it lives (`test_the_scheduled_dispatch_can_be_driven.py`), and it is NOT extended to
chat or Code here: the Usage row says "scheduled tasks only" because that is what it does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.api.config_api import APPLIES_WHEN, is_editable, patch_config, read_config
from chimera.config import Settings, get_settings


def test_the_screen_may_write_it() -> None:
    assert is_editable("CHIMERA_DAILY_USD_CAP")


def test_it_applies_from_the_next_scheduled_job_not_a_relaunch() -> None:
    """The daemon asks for the cap per dispatch (`make_run_job(daily_cap=...)`), so no APPLIES_WHEN
    entry. That this is true, and not only declared, is the test below."""
    assert "CHIMERA_DAILY_USD_CAP" not in APPLIES_WHEN


def test_a_cap_saved_from_the_screen_brakes_the_running_daemons_next_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect the first version shipped: the daemon held the `Settings` it was started with, so
    a cap saved from the Usage row while the app ran braked nothing until a relaunch — and the
    screen, and `GET /api/config`, said it was set. Driven through the very `run_job` that
    `_start_cron_daemon` builds, with only the thread and the network faked."""
    import json
    import threading
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from typing import Any

    import chimera.scheduler as scheduler_pkg
    import chimera.scheduler.job_runner as job_runner
    from chimera.cli.main import _start_cron_daemon
    from chimera.core import keep_awake as ka
    from chimera.orchestration.budget import BudgetExceeded
    from chimera.scheduler.models import CronJob
    from tests.test_the_scheduled_dispatch_can_be_driven import _Backend

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_DAILY_USD_CAP", "")  # no cap when the daemon starts
    get_settings.cache_clear()

    built: list[Any] = []
    real_make_run_job = job_runner.make_run_job

    def spy(**kwargs: Any) -> Any:
        run_job = real_make_run_job(**kwargs)
        built.append(run_job)
        return run_job

    class _NoThread:
        def __init__(self, *_a: Any, **_k: Any) -> None: ...

        def start(self) -> tuple[None, threading.Event]:
            return None, threading.Event()

    monkeypatch.setattr(job_runner, "make_run_job", spy)
    monkeypatch.setattr(scheduler_pkg, "CronDaemon", _NoThread)
    monkeypatch.setattr(ka, "_service", ka.KeepAwake(settings=lambda: SimpleNamespace(keep_awake="off")))

    backend = _Backend()
    _start_cron_daemon(backend, "fake/model", 3, tmp_path, 30)  # type: ignore[arg-type]  # a fake backend
    (run_job,) = built
    job = CronJob(id="j", name="n", trigger="cron", schedule="* * * * *", action="x")

    # Before the save there is no cap, so the job runs: the half that proves the refusal below is
    # about the save.
    run_job(job)
    assert backend.calls > 0

    # Today's spend, now far past any cap the screen could save — and priced, because the fake
    # model's own row is unpriced and that refuses for a different reason.
    (home / "usage.jsonl").write_text(
        json.dumps({"ts": datetime.now(UTC).isoformat(), "session_id": "x", "usd": 999.0}) + "\n",
        encoding="utf-8",
    )
    patch_config({"CHIMERA_DAILY_USD_CAP": "1.5"}, env_path=tmp_path / ".env")

    antes = backend.calls
    with pytest.raises(BudgetExceeded, match="daily cap reached"):
        run_job(job)
    assert backend.calls == antes, "the job spent past a cap the screen said was set"


def test_the_screen_reads_the_cap_or_its_absence(tmp_path: Path) -> None:
    capped = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_DAILY_USD_CAP="2.5")  # type: ignore[call-arg]

    assert read_config(capped)["spend"] == {"daily_usd_cap": 2.5}
    assert read_config(Settings(CHIMERA_HOME=str(tmp_path)))["spend"] == {"daily_usd_cap": None}  # type: ignore[call-arg]


@pytest.mark.parametrize("value", ["0", "-1", "abc", "nan", "inf", "1e999"])
def test_a_value_that_would_brake_nothing_or_break_the_app_is_refused(
    tmp_path: Path, value: str
) -> None:
    """Zero is the subtle one: the runner reads the cap with `if cap`, so `0` would be saved, shown
    as a $0.00 cap, and stop nothing. A value that does not parse would make `Settings` fail to build
    on the next read and take the whole app with it."""
    env = tmp_path / ".env"

    with pytest.raises(ValueError, match="CHIMERA_DAILY_USD_CAP"):
        patch_config({"CHIMERA_DAILY_USD_CAP": value}, env_path=env)

    assert not env.exists()


def test_a_saved_cap_reaches_the_settings_the_runner_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIMERA_DAILY_USD_CAP", "")  # own the name, so the teardown restores it
    env = tmp_path / ".env"

    patch_config({"CHIMERA_DAILY_USD_CAP": "3.75"}, env_path=env)

    assert "CHIMERA_DAILY_USD_CAP=3.75" in env.read_text(encoding="utf-8")
    assert get_settings().daily_usd_cap == 3.75


def test_clearing_the_field_removes_the_cap_without_breaking_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saving the field empty writes `CHIMERA_DAILY_USD_CAP=`, which pydantic used to read as a
    float that failed to parse — every later settings read would have raised."""
    monkeypatch.setenv("CHIMERA_DAILY_USD_CAP", "5")
    env = tmp_path / ".env"

    patch_config({"CHIMERA_DAILY_USD_CAP": ""}, env_path=env)

    assert get_settings().daily_usd_cap is None
    assert Settings(CHIMERA_DAILY_USD_CAP="  ").daily_usd_cap is None  # type: ignore[call-arg]
