"""The machine is held awake while there is work, let go when there is none, and never touched
when the owner left the switch off (study 29, P2.5; `chimera/core/keep_awake.py`).

Nothing stopped a laptop from sleeping under a running agent: a coding turn, a background work, a
schedule due at 7 a.m. all died with the screen, and the cron watchdog could only report the
silence afterwards. These tests never call the real operating system: every inhibitor is a fake
handed in through the factory seam, and the Windows one is driven with a fake ``kernel32`` — so
they say the same thing on the Windows machine and in WSL.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.core import keep_awake as ka
from chimera.core.keep_awake import (
    ES_CONTINUOUS,
    ES_SYSTEM_REQUIRED,
    KeepAwake,
    NullInhibitor,
    SystemdInhibitor,
    WindowsInhibitor,
    cron_due_probe,
    default_inhibitor,
)

TIMEOUT = 10.0


class _FakeInhibitor:
    name = "fake"

    def __init__(self, *, battery: bool | None = False) -> None:
        self.calls: list[tuple[str, int]] = []
        self.battery = battery

    def acquire(self, why: str) -> None:
        self.calls.append(("acquire", threading.get_ident()))

    def release(self) -> None:
        self.calls.append(("release", threading.get_ident()))

    def on_battery(self) -> bool | None:
        return self.battery

    @property
    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.calls]


def _settings(mode: str = "working", *, on_battery: bool = False) -> SimpleNamespace:
    return SimpleNamespace(keep_awake=mode, keep_awake_on_battery=on_battery)


def _keeper(fake: _FakeInhibitor, settings: SimpleNamespace) -> KeepAwake:
    return KeepAwake(settings=lambda: settings, inhibitor_factory=lambda: fake)


def _wait(predicate: Any) -> bool:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------------------------------- counter


def test_two_holds_keep_the_machine_up_until_the_last_one_ends() -> None:
    fake = _FakeInhibitor()
    keeper = _keeper(fake, _settings())

    keeper.acquire("turn")
    keeper.acquire("turn")
    assert keeper.tick().active
    keeper.release("turn")
    assert keeper.tick().active, "one turn still running and the machine was let go"
    keeper.release("turn")
    state = keeper.tick()

    assert not state.active
    assert fake.kinds == ["acquire", "release"], "the OS is asked once to hold and once to let go"


def test_an_extra_release_cannot_drive_the_count_below_zero() -> None:
    """A negative count would make the next turn's acquire read as zero, and the machine would
    sleep under it."""
    fake = _FakeInhibitor()
    keeper = _keeper(fake, _settings())

    keeper.release("turn")
    keeper.acquire("turn")

    assert keeper.count() == 1
    assert keeper.tick().active


def test_the_hold_is_released_when_the_work_raises() -> None:
    fake = _FakeInhibitor()
    keeper = _keeper(fake, _settings())

    with pytest.raises(RuntimeError), keeper.hold("work"):
        assert keeper.count() == 1
        raise RuntimeError("the turn died")

    assert keeper.count() == 0


def test_the_status_names_what_keeps_the_machine_up() -> None:
    keeper = _keeper(_FakeInhibitor(), _settings())
    keeper.acquire("work")
    keeper.add_probe("cron", lambda: 1)
    keeper.add_probe("run", lambda: 0)

    state = keeper.tick()

    assert state.reasons == ("cron", "work")
    assert state.to_dict()["reasons"] == ["cron", "work"]


def test_a_probe_that_raises_does_not_stop_the_keeper() -> None:
    def broken() -> int:
        raise OSError("the store moved")

    keeper = _keeper(_FakeInhibitor(), _settings())
    keeper.add_probe("cron", broken)
    keeper.acquire("turn")

    assert keeper.tick().reasons == ("turn",)


# ---------------------------------------------------------------------------------------- the modes


def test_off_never_builds_or_calls_the_os_mechanism() -> None:
    """The default. Not "acquire and do nothing": with the switch off the factory is never asked for
    an inhibitor, so not even the battery state is read."""
    built: list[str] = []

    def factory() -> Any:
        built.append("built")
        raise AssertionError("off must not reach the operating system")

    keeper = KeepAwake(settings=lambda: _settings("off"), inhibitor_factory=factory)
    keeper.acquire("turn")
    keeper.add_probe("cron", lambda: 3)

    state = keeper.tick()

    assert built == []
    assert not state.active and state.reasons == () and state.mode == "off"


def test_off_never_calls_set_thread_execution_state() -> None:
    """The same promise against the real Windows class, with ``kernel32`` replaced: a turn running
    under ``off`` produces no call to the API at all."""

    class _Kernel32:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def SetThreadExecutionState(self, flags: int) -> int:  # noqa: N802 — the Win32 name
            self.calls.append(f"set {flags:#x}")
            return 1

        def GetSystemPowerStatus(self, _status: Any) -> int:  # noqa: N802
            self.calls.append("power")
            return 0

    kernel32 = _Kernel32()
    keeper = KeepAwake(
        settings=lambda: _settings("off"), inhibitor_factory=lambda: WindowsInhibitor(kernel32)
    )
    keeper.acquire("turn")
    keeper.tick()

    assert kernel32.calls == []


def test_working_asks_windows_for_the_system_and_releases_with_continuous_alone() -> None:
    class _Kernel32:
        def __init__(self) -> None:
            self.flags: list[int] = []

        def SetThreadExecutionState(self, flags: int) -> int:  # noqa: N802 — the Win32 name
            self.flags.append(flags)
            return 1

    kernel32 = _Kernel32()
    inhibitor = WindowsInhibitor(kernel32)
    inhibitor.acquire("Chimera: turn")
    inhibitor.release()

    assert kernel32.flags == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED, ES_CONTINUOUS]


def test_always_holds_with_no_work_at_all() -> None:
    fake = _FakeInhibitor()
    state = _keeper(fake, _settings("always")).tick()

    assert state.active and state.reasons == ("always",)


def test_switching_off_while_held_lets_the_machine_go() -> None:
    fake = _FakeInhibitor()
    settings = _settings()
    keeper = _keeper(fake, settings)
    keeper.acquire("turn")
    assert keeper.tick().active

    settings.keep_awake = "off"
    state = keeper.tick()

    assert not state.active
    assert fake.kinds == ["acquire", "release"]


def test_an_unknown_mode_reads_as_off() -> None:
    fake = _FakeInhibitor()
    keeper = _keeper(fake, _settings("forever"))
    keeper.acquire("turn")

    assert keeper.tick().mode == "off"
    assert fake.calls == []


# ---------------------------------------------------------------------------------------- battery


def test_on_battery_the_machine_is_not_held_unless_the_owner_allowed_it() -> None:
    fake = _FakeInhibitor(battery=True)
    settings = _settings()
    keeper = _keeper(fake, settings)
    keeper.acquire("turn")

    state = keeper.tick()
    assert not state.active and state.blocked == "battery"
    assert fake.kinds == []

    settings.keep_awake_on_battery = True
    state = keeper.tick()
    assert state.active and state.blocked == ""


def test_unplugging_mid_turn_lets_the_machine_go() -> None:
    fake = _FakeInhibitor(battery=False)
    keeper = _keeper(fake, _settings())
    keeper.acquire("turn")
    assert keeper.tick().active

    fake.battery = True

    assert not keeper.tick().active
    assert fake.kinds == ["acquire", "release"]


# ---------------------------------------------------------------------------------------- platforms


def test_no_mechanism_off_windows_and_linux_desktops() -> None:
    assert isinstance(default_inhibitor("darwin", {}, lambda _n: None), NullInhibitor)
    assert isinstance(default_inhibitor("freebsd14", {}, lambda _n: "/usr/bin/x"), NullInhibitor)


def test_a_headless_linux_server_gets_the_null_inhibitor_even_with_systemd() -> None:
    """A VPS has `systemd-inhibit` and nothing that sleeps; a lock there is a process doing nothing."""
    inhibitor = default_inhibitor("linux", {}, lambda _n: "/usr/bin/systemd-inhibit")
    assert isinstance(inhibitor, NullInhibitor)


def test_a_linux_desktop_with_systemd_inhibit_gets_it() -> None:
    which = lambda _n: "/usr/bin/systemd-inhibit"  # noqa: E731
    assert isinstance(default_inhibitor("linux", {"DISPLAY": ":0"}, which), SystemdInhibitor)
    assert isinstance(
        default_inhibitor("linux", {"XDG_SESSION_TYPE": "wayland"}, which), SystemdInhibitor
    )
    assert isinstance(default_inhibitor("linux", {"DISPLAY": ":0"}, lambda _n: None), NullInhibitor)


def test_windows_gets_the_execution_state_mechanism() -> None:
    # Construction touches nothing: `kernel32` is resolved on first use, never here.
    assert isinstance(default_inhibitor("win32", {}, lambda _n: None), WindowsInhibitor)


def test_where_nothing_can_hold_the_status_says_so_instead_of_claiming_it() -> None:
    keeper = KeepAwake(settings=lambda: _settings(), inhibitor_factory=NullInhibitor)
    keeper.acquire("turn")

    state = keeper.tick()

    assert not state.active
    assert state.blocked == "unsupported" and state.mechanism == "none"


def test_systemd_inhibit_dies_with_this_process(tmp_path: Path) -> None:
    """`tail --pid=<us>`, not `sleep infinity`: an orphaned lock would hold the machine forever."""
    launched: list[list[str]] = []

    class _Proc:
        terminated = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: float) -> int:
            return 0

    proc = _Proc()

    def popen(argv: list[str], **_kw: Any) -> _Proc:
        launched.append(argv)
        return proc

    inhibitor = SystemdInhibitor("/usr/bin/systemd-inhibit", popen=popen, power_supply=tmp_path)
    inhibitor.acquire("Chimera: turn")
    inhibitor.acquire("Chimera: turn")  # already held: no second lock
    inhibitor.release()

    [argv] = launched
    assert "--what=sleep:idle" in argv and "--mode=block" in argv
    assert argv[argv.index("tail") + 1].startswith("--pid=")
    assert proc.terminated


def test_linux_reads_battery_from_the_mains_supply(tmp_path: Path) -> None:
    mains = tmp_path / "AC"
    mains.mkdir()
    (mains / "type").write_text("Mains\n", encoding="utf-8")
    (mains / "online").write_text("0\n", encoding="utf-8")
    inhibitor = SystemdInhibitor("x", power_supply=tmp_path)

    assert inhibitor.on_battery() is True
    (mains / "online").write_text("1\n", encoding="utf-8")
    assert inhibitor.on_battery() is False
    assert SystemdInhibitor("x", power_supply=tmp_path / "missing").on_battery() is None


# ---------------------------------------------------------------------------------------- cron


def test_a_schedule_due_soon_or_running_now_counts_and_a_far_one_does_not() -> None:
    now = 1_000_000.0

    def job(next_run: float | None, *, enabled: bool = True, trigger: str = "cron") -> Any:
        return SimpleNamespace(next_run=next_run, enabled=enabled, trigger=trigger)

    jobs = [
        job(now + 60),  # due in a minute
        job(now - 5),  # overdue: the one running now, whose next_run advances after it returns
        job(now + 3 * 3600),  # tonight
        job(now + 30, enabled=False),
        job(now + 30, trigger="event"),
        job(None),
    ]
    scheduler = SimpleNamespace(store=SimpleNamespace(list=lambda: jobs))

    assert cron_due_probe(scheduler, clock=lambda: now)() == 2


# ---------------------------------------------------------------------------------------- the thread


def test_the_os_is_only_ever_called_from_the_keepers_own_thread() -> None:
    """On Windows the hold belongs to the thread that set it, so a call from the request thread
    that started a turn would be released whenever that thread happened to end."""
    fake = _FakeInhibitor()
    keeper = KeepAwake(settings=lambda: _settings(), inhibitor_factory=lambda: fake, poll=0.05)
    keeper.start()
    try:
        keeper.acquire("turn")
        assert _wait(lambda: keeper.state().active)
        keeper.release("turn")
        assert _wait(lambda: not keeper.state().active)
    finally:
        keeper.shutdown()

    threads = {ident for _, ident in fake.calls}
    assert fake.kinds == ["acquire", "release"]
    assert threads and threading.get_ident() not in threads and len(threads) == 1


def test_shutting_down_while_held_lets_the_machine_go() -> None:
    fake = _FakeInhibitor()
    keeper = KeepAwake(settings=lambda: _settings("always"), inhibitor_factory=lambda: fake, poll=0.05)
    keeper.start()
    assert _wait(lambda: keeper.state().active)

    keeper.shutdown()

    assert fake.kinds == ["acquire", "release"]


def test_with_the_switch_off_the_process_keeper_runs_no_thread_until_it_is_turned_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every process that builds the API builds a keeper — the whole test suite included. A thread
    reading the settings four times a minute from the background, with the switch off, raced every
    test that changes the environment: on an empty cache that read exports `.env` credentials into
    `os.environ` and caches whatever the environment holds at that instant. Off costs nothing now,
    and the screen's switch is what brings the thread up (and, turned off again, lets it end)."""
    fake = _FakeInhibitor()
    settings = _settings("off")
    keeper = KeepAwake(settings=lambda: settings, inhibitor_factory=lambda: fake, poll=0.05)
    monkeypatch.setattr(ka, "_service", keeper)
    try:
        assert ka.service() is keeper
        assert not keeper.running(), "a keeper thread started with the switch off"

        settings.keep_awake = "working"
        keeper.nudge()  # what `PATCH /api/config` does when either switch is saved
        assert keeper.running()
        keeper.acquire("turn")
        assert _wait(lambda: keeper.state().active)

        settings.keep_awake = "off"
        keeper.nudge()
        assert _wait(lambda: not keeper.running()), "the thread outlived the switch"
        assert not keeper.state().active
        assert fake.kinds == ["acquire", "release"]
    finally:
        keeper.release("turn")
        keeper.shutdown()


# ---------------------------------------------------------------------------------------- the app


@pytest.fixture
def app_keeper(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The process's keeper, replaced by one on a fake inhibitor in `working` mode."""
    fake = _FakeInhibitor()
    keeper = KeepAwake(settings=lambda: _settings(), inhibitor_factory=lambda: fake, poll=0.05)
    monkeypatch.setattr(ka, "_service", keeper)
    yield keeper
    keeper.shutdown()


def test_a_coding_turn_holds_the_machine_while_it_runs_and_the_status_route_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app_keeper: KeepAwake
) -> None:
    from tests.test_a_turn_can_be_found_after_the_screen_leaves_it import _agent_class, _client

    entered, release = threading.Event(), threading.Event()
    client = _client(tmp_path, monkeypatch, _agent_class(entered, release))
    turn = threading.Thread(
        target=lambda: client.post("/api/code/turn", json={"message": "audit the gateway"}),
        daemon=True,
    )
    turn.start()
    try:
        # Generous: the first turn in a process imports most of the product on Windows.
        assert entered.wait(60), "the turn never reached the agent"
        assert app_keeper.count() == 1
        assert _wait(lambda: client.get("/api/keep-awake").json()["active"])
        body = client.get("/api/keep-awake").json()
        assert body["reasons"] == ["turn"] and body["mode"] == "working"
    finally:
        release.set()
        turn.join(TIMEOUT)

    assert app_keeper.count() == 0
    assert _wait(lambda: not app_keeper.state().active)


def test_an_autonomous_run_still_cancellable_keeps_the_machine_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app_keeper: KeepAwake
) -> None:
    """Runs are counted from the map of cancellable runs, which registers a run before its worker
    starts and drops it in the worker's `finally`."""
    import chimera.api.app as app_module
    from tests.test_a_turn_can_be_found_after_the_screen_leaves_it import _agent_class, _client

    _client(tmp_path, monkeypatch, _agent_class(threading.Event(), threading.Event()))
    monkeypatch.setitem(app_module._run_cancels, "run-1", threading.Event())

    assert "run" in app_keeper.tick().reasons
    del app_module._run_cancels["run-1"]
    assert "run" not in app_keeper.tick().reasons


def test_settings_read_a_typo_as_off_and_accept_the_three_words() -> None:
    from chimera.config import Settings

    assert Settings(CHIMERA_KEEP_AWAKE="forever").keep_awake == "off"  # type: ignore[call-arg]
    assert Settings(CHIMERA_KEEP_AWAKE="").keep_awake == "off"  # type: ignore[call-arg]
    assert Settings(CHIMERA_KEEP_AWAKE=" Working ").keep_awake == "working"  # type: ignore[call-arg]
    assert Settings().keep_awake == "off"
    assert Settings().keep_awake_on_battery is False


def test_the_screen_may_write_both_switches_and_reads_them_back(tmp_path: Path) -> None:
    from chimera.api.config_api import is_editable, patch_config, read_config
    from chimera.config import Settings

    assert is_editable("CHIMERA_KEEP_AWAKE") and is_editable("CHIMERA_KEEP_AWAKE_ON_BATTERY")
    with pytest.raises(ValueError, match="off, working, always"):
        patch_config({"CHIMERA_KEEP_AWAKE": "forever"}, env_path=tmp_path / ".env")
    with pytest.raises(ValueError, match="true or false"):
        patch_config({"CHIMERA_KEEP_AWAKE_ON_BATTERY": "maybe"}, env_path=tmp_path / ".env")
    assert not (tmp_path / ".env").exists(), "a refused value was written"

    chosen = Settings(CHIMERA_KEEP_AWAKE="always", CHIMERA_KEEP_AWAKE_ON_BATTERY="1")  # type: ignore[call-arg]
    assert read_config(chosen)["keep_awake"] == {"mode": "always", "on_battery": True}


def test_a_running_scheduled_job_holds_the_machine_whatever_fired_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app_keeper: KeepAwake
) -> None:
    """The cron probe looks ahead by `next_run`, which only clock jobs have, so a webhook or event job
    that was already running held nothing while the hint said "a scheduled task". Each of the three
    dispatch paths is driven for real here — the daemon's, the webhook handler's and `cron fire` —
    with only the agent behind them faked, and each must hold while the job runs and let go after."""
    from typer.testing import CliRunner

    import chimera.scheduler as scheduler_pkg
    import chimera.scheduler.job_runner as job_runner
    from chimera.cli.main import _cron_store, _start_cron_daemon, _webhook_handler, app
    from chimera.config import get_settings
    from chimera.scheduler import Scheduler
    from chimera.scheduler.models import CronJob, JobOutcome

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    seen: list[tuple[str, int]] = []

    def fake_make_run_job(**_kwargs: Any) -> Any:
        def run_job(job: CronJob) -> JobOutcome:
            seen.append((job.trigger, app_keeper.count()))
            return JobOutcome(answer="feito")

        return run_job

    dispatches: list[Any] = []

    class _NoThread:
        def __init__(self, _scheduler: Any, dispatch: Any, **_kwargs: Any) -> None:
            dispatches.append(dispatch)

        def start(self) -> tuple[None, threading.Event]:
            return None, threading.Event()

    monkeypatch.setattr(job_runner, "make_run_job", fake_make_run_job)
    monkeypatch.setattr(scheduler_pkg, "CronDaemon", _NoThread)
    monkeypatch.setattr("chimera.providers.LLMGateway", lambda *_a, **_k: object())

    # The daemon's dispatch, for a clock job and for an event job handed to it.
    _start_cron_daemon(object(), "fake/model", 3, tmp_path, 30)  # type: ignore[arg-type]  # a fake backend
    (dispatch,) = dispatches
    for trigger in ("cron", "event"):
        dispatch(CronJob(id=trigger, name=trigger, trigger=trigger, schedule="* * * * *", action="x"))  # type: ignore[arg-type]  # the literal is one of the three

    # A webhook job, through the handler `chimera serve` mounts.
    scheduler = Scheduler(_cron_store())
    scheduler.schedule_webhook("on push", "gh-push", "Summarise the push.")

    class _Gateway:
        def on_message(self, _message: Any) -> str:
            seen.append(("webhook", app_keeper.count()))
            return "ok"

    _webhook_handler(_Gateway())("gh-push", {})  # type: ignore[arg-type]  # a fake gateway

    # An event job, through `chimera cron fire`.
    scheduler.schedule_event("on deploy", "deploy", "Check the deploy.")
    result = CliRunner().invoke(app, ["cron", "fire", "deploy"])
    assert result.exit_code == 0, result.output

    assert seen == [("cron", 1), ("event", 1), ("webhook", 1), ("event", 1)]
    assert app_keeper.count() == 0, "a dispatch that ended did not let the machine go"
