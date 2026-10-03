"""Every on/off switch study 29 shipped can be changed from the Settings screen.

The owner's decision: a switch that only `.env`, a JSON file or the tray menu can change is a switch
most people never find. These are the server halves of the rows that were missing —

- `CHIMERA_CRON_NOTIFY_FAILURES` was writable through `PATCH /api/config` and never READ back, so
  the screen could not show it;
- `CHIMERA_ARCHIVE_AFTER_DAYS` was neither;
- the tray's switches lived in the shell's own file, which the window cannot reach (no IPC), so the
  backend now writes that file on the screen's behalf (`chimera/api/shell_prefs.py`);
- the weekly review existed only as `chimera report weekly` plus `chimera cron enable`.

And the one switch that is deliberately NOT on the screen: `CHIMERA_APPROVE_VIA_CHAT`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chimera.api.config_api import APPLIES_WHEN, is_editable, patch_config, read_config
from chimera.api.shell_prefs import (
    PREFS_ENV,
    STATE_ENV,
    ShellPrefsBusy,
    ShellPrefsUnreadable,
    ShellUnavailable,
    parse_prefs,
    read_shell_prefs,
    write_shell_prefs,
)
from chimera.config import Settings, get_settings
from chimera.scheduler.models import CronJob
from chimera.scheduler.store import CronStore
from chimera.scheduler.weekly_review import BUILTIN_KEY, WEEKLY_REVIEW, find_proposal

# --- the one that stays off the screen ---------------------------------------------------------


def test_approving_from_a_chat_bot_is_not_a_switch_any_screen_can_flip(tmp_path: Path) -> None:
    """Turning it on widens who can approve to whoever holds the chat channel. The owner decided
    that must stay a deliberate edit of `.env`, so the endpoint every Settings row writes through —
    and the desktop bridge's `settings.edit`, which is the same endpoint — refuses the key."""
    assert not is_editable("CHIMERA_APPROVE_VIA_CHAT")
    env = tmp_path / ".env"
    with pytest.raises(ValueError, match="not editable: CHIMERA_APPROVE_VIA_CHAT"):
        patch_config({"CHIMERA_APPROVE_VIA_CHAT": "true"}, env_path=env)
    assert not env.exists()


# --- the cron failure notice -------------------------------------------------------------------


def test_the_cron_failure_notice_is_read_back_so_the_screen_can_show_it(tmp_path: Path) -> None:
    on = Settings(CHIMERA_HOME=str(tmp_path))  # type: ignore[call-arg]
    off = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_CRON_NOTIFY_FAILURES="false")  # type: ignore[call-arg]

    assert is_editable("CHIMERA_CRON_NOTIFY_FAILURES")
    assert read_config(on)["automation"]["notify_failures"] is True, "it ships on"
    assert read_config(off)["automation"]["notify_failures"] is False
    # Read on every tick of the daemon, so a save applies to the next notice, not the next launch.
    assert "CHIMERA_CRON_NOTIFY_FAILURES" not in APPLIES_WHEN


def test_a_saved_notice_switch_reaches_the_settings_the_daemon_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIMERA_CRON_NOTIFY_FAILURES", "true")  # own the name for the teardown
    patch_config({"CHIMERA_CRON_NOTIFY_FAILURES": "false"}, env_path=tmp_path / ".env")
    assert get_settings().cron_notify_failures is False


def test_a_notice_value_the_app_could_not_start_on_is_refused(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    with pytest.raises(ValueError, match="CHIMERA_CRON_NOTIFY_FAILURES must be true or false"):
        patch_config({"CHIMERA_CRON_NOTIFY_FAILURES": "sometimes"}, env_path=env)
    assert not env.exists()


# --- archiving idle conversations --------------------------------------------------------------


def test_archiving_is_read_back_as_a_number_or_never(tmp_path: Path) -> None:
    never = Settings(CHIMERA_HOME=str(tmp_path))  # type: ignore[call-arg]
    weekly = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_ARCHIVE_AFTER_DAYS="7")  # type: ignore[call-arg]

    assert is_editable("CHIMERA_ARCHIVE_AFTER_DAYS")
    assert read_config(never)["conversations"] == {"archive_after_days": None}
    assert read_config(weekly)["conversations"] == {"archive_after_days": 7.0}
    # The list asks for it on every look, so the save applies at once.
    assert "CHIMERA_ARCHIVE_AFTER_DAYS" not in APPLIES_WHEN


@pytest.mark.parametrize("value", ["0", "-3", "a week", "nan", "inf", "1e999"])
def test_a_number_of_days_that_would_archive_nothing_is_refused(tmp_path: Path, value: str) -> None:
    """Zero is the subtle one: `due_for_archive` reads `if not after_days`, so `0` would be saved,
    shown as a number, and archive nothing. `Settings` survives a word (it falls back to never),
    which is exactly why the screen must not be allowed to confirm one."""
    env = tmp_path / ".env"
    with pytest.raises(ValueError, match="CHIMERA_ARCHIVE_AFTER_DAYS"):
        patch_config({"CHIMERA_ARCHIVE_AFTER_DAYS": value}, env_path=env)
    assert not env.exists()


def test_a_saved_number_of_days_reaches_the_rule_and_empty_is_never_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIMERA_ARCHIVE_AFTER_DAYS", "")
    env = tmp_path / ".env"

    patch_config({"CHIMERA_ARCHIVE_AFTER_DAYS": "14"}, env_path=env)
    assert get_settings().archive_after_days == 14.0

    patch_config({"CHIMERA_ARCHIVE_AFTER_DAYS": ""}, env_path=env)
    assert get_settings().archive_after_days is None


# --- the tray's switches -----------------------------------------------------------------------


@pytest.fixture
def shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """The two paths the desktop shell hands the backend it starts (`start_sidecar` in main.rs)."""
    prefs = tmp_path / "shell-prefs.json"
    state = tmp_path / "shell-state.json"
    monkeypatch.setenv(PREFS_ENV, str(prefs))
    monkeypatch.setenv(STATE_ENV, str(state))
    return prefs, state


def test_without_the_desktop_shell_there_is_no_tray_to_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backend started by `chimera app` in a browser, or by a server, has no shell: the screen
    says so instead of writing a file no process reads."""
    monkeypatch.delenv(PREFS_ENV, raising=False)
    monkeypatch.delenv(STATE_ENV, raising=False)

    assert read_shell_prefs()["available"] is False
    with pytest.raises(ShellUnavailable):
        write_shell_prefs({"keep_in_tray": True})
    assert list(tmp_path.iterdir()) == []


def test_a_first_run_reads_as_the_shells_own_defaults(shell: tuple[Path, Path]) -> None:
    """The defaults are `Prefs::default()` in prefs.rs: tray and shortcut off, the flash on. A copy
    that disagreed would describe a window the shell is not running."""
    got = read_shell_prefs()
    assert got["available"] is True
    assert (got["keep_in_tray"], got["call_attention"], got["quick_entry"]) == (False, True, False)
    assert got["quick_entry_chord"] == "CommandOrControl+Shift+Space"
    assert got["start_at_sign_in"] is None, "no report yet is unknown, not off"


def test_a_switch_saved_from_the_screen_is_written_where_the_shell_reads_it(
    shell: tuple[Path, Path],
) -> None:
    """The chord the owner typed by hand, and a key from a newer shell, survive the save: the
    screen changes the switches it was asked to change and nothing else in the file."""
    prefs, _state = shell
    prefs.write_text(
        json.dumps({"quick_entry_chord": "Alt+Q", "from_a_newer_shell": 3}), encoding="utf-8"
    )

    got = write_shell_prefs({"keep_in_tray": True, "call_attention": False})

    on_disk = json.loads(prefs.read_text(encoding="utf-8"))
    assert on_disk == {
        "quick_entry_chord": "Alt+Q",
        "from_a_newer_shell": 3,
        "keep_in_tray": True,
        "call_attention": False,
    }
    assert (got["keep_in_tray"], got["call_attention"], got["quick_entry_chord"]) == (
        True,
        False,
        "Alt+Q",
    )


def test_start_at_sign_in_is_asked_for_and_shown_as_the_os_answered(
    shell: tuple[Path, Path],
) -> None:
    """Its truth is the operating system's. The screen writes a request; what it shows is the
    shell's report of the OS's answer — so a refused sign-in entry reads as off, not as saved."""
    prefs, state = shell
    state.write_text(json.dumps({"start_at_sign_in": False, "problem": ""}), encoding="utf-8")

    got = write_shell_prefs({"start_at_sign_in": True})

    assert json.loads(prefs.read_text(encoding="utf-8"))["start_at_sign_in"] is True
    assert got["sign_in_requested"] is True
    assert got["start_at_sign_in"] is False, "the request was reported as the OS's answer"

    # The shell carried it out, removed the request, and reported the refusal it got.
    prefs.write_text("{}", encoding="utf-8")
    state.write_text(
        json.dumps({"start_at_sign_in": False, "problem": "sign-in entry refused: policy"}),
        encoding="utf-8",
    )
    after = read_shell_prefs()
    assert after["sign_in_requested"] is None
    assert after["start_at_sign_in"] is False
    assert after["problem"] == "sign-in entry refused: policy"


def test_a_hand_edit_that_does_not_parse_is_never_overwritten_from_here(
    shell: tuple[Path, Path],
) -> None:
    """The shell sets such a file aside before its own save; this side cannot without racing it,
    so it refuses, and reports the shell's defaults as what is in force — which they are."""
    prefs, _state = shell
    edit = '{ "quick_entry_chord": "Ctrl+Alt+K", }'
    prefs.write_text(edit, encoding="utf-8")

    got = read_shell_prefs()
    assert got["unreadable"] is True
    assert got["keep_in_tray"] is False and got["call_attention"] is True

    with pytest.raises(ShellPrefsUnreadable, match="cannot read"):
        write_shell_prefs({"keep_in_tray": True})
    assert prefs.read_text(encoding="utf-8") == edit


def test_only_the_switches_are_writable_never_the_chord(shell: tuple[Path, Path]) -> None:
    """The chord is free text in the accelerator syntax, and a typo takes a key combination from
    every other program on the machine. It stays a hand edit."""
    with pytest.raises(ValueError, match="not a shell switch: quick_entry_chord"):
        write_shell_prefs({"quick_entry_chord": True})
    with pytest.raises(ValueError, match="keep_in_tray must be true or false"):
        write_shell_prefs({"keep_in_tray": "yes"})  # type: ignore[dict-item]  # what a bad caller sends


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from chimera.api.app import build_api_app

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    return TestClient(
        build_api_app(lambda: None, settings=Settings(CHIMERA_HOME=str(home)))  # type: ignore[arg-type,call-arg]  # no backend needed
    )


def test_the_route_writes_the_switches_and_refuses_the_chord(
    client: TestClient, shell: tuple[Path, Path]
) -> None:
    prefs, _state = shell

    assert client.get("/api/shell/prefs").json()["available"] is True
    saved = client.patch("/api/shell/prefs", json={"quick_entry": True})
    assert saved.status_code == 200, saved.text
    assert saved.json()["quick_entry"] is True
    assert json.loads(prefs.read_text(encoding="utf-8")) == {"quick_entry": True}

    assert client.patch("/api/shell/prefs", json={"quick_entry_chord": "Alt+Q"}).status_code == 422


def test_the_route_says_409_where_no_shell_started_the_server(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(PREFS_ENV, raising=False)
    refused = client.patch("/api/shell/prefs", json={"keep_in_tray": True})
    assert refused.status_code == 409
    assert "not started by the desktop app" in refused.json()["detail"]


# --- the weekly review -------------------------------------------------------------------------


def _jobs(tmp_path: Path) -> list[CronJob]:
    """The jobs as stored now. A `CronStore` reads its file once, so it is opened per look."""
    return CronStore(tmp_path / "home" / "scheduler" / "jobs.json").list()


def test_switching_the_weekly_review_on_proposes_it_once_and_enables_it(
    client: TestClient, tmp_path: Path
) -> None:
    assert client.get("/api/cron/weekly-review").json() == {
        "proposed": False,
        "job_id": "",
        "enabled": False,
        "posts_to": "",
    }
    on = client.put("/api/cron/weekly-review", json={"enabled": True}).json()
    again = client.put("/api/cron/weekly-review", json={"enabled": True}).json()

    jobs = _jobs(tmp_path)
    assert len(jobs) == 1, "switching it on twice made two weekly reviews"
    (job,) = jobs
    assert job.metadata.get(BUILTIN_KEY) == WEEKLY_REVIEW, "it is not the code-counted review"
    assert job.enabled
    assert job.created_by == "human", "the owner's switch recorded the job as an agent proposal"
    assert on == again == {"proposed": True, "job_id": job.id, "enabled": True, "posts_to": ""}


def test_a_review_the_owner_asks_for_is_created_as_theirs(tmp_path: Path) -> None:
    """`propose(created_by="human")` is what the screen's route calls when no job exists yet: the
    job is the owner's from the start (and, like every human-created job, enabled)."""
    from chimera.scheduler import Scheduler
    from chimera.scheduler.weekly_review import propose

    store = CronStore(tmp_path / "jobs.json")
    job, created = propose(Scheduler(store), now=0.0, created_by="human")
    assert created and job.created_by == "human" and job.enabled


def test_adopting_an_agent_proposal_from_the_screen_makes_it_the_owners(
    client: TestClient, tmp_path: Path
) -> None:
    """`chimera report weekly` proposes the job as the agent's, disabled. Switching it on from
    Settings is the owner taking it: the record says human, as `cron add` would have."""
    from chimera.scheduler import Scheduler
    from chimera.scheduler.weekly_review import propose

    store = CronStore(tmp_path / "home" / "scheduler" / "jobs.json")
    proposal, _ = propose(Scheduler(store), now=0.0)
    assert proposal.created_by == "agent" and not proposal.enabled

    client.put("/api/cron/weekly-review", json={"enabled": True})

    (job,) = _jobs(tmp_path)
    assert job.id == proposal.id and job.enabled and job.created_by == "human"


def test_switching_it_off_never_creates_one_and_pauses_one_that_exists(
    client: TestClient, tmp_path: Path
) -> None:
    off = client.put("/api/cron/weekly-review", json={"enabled": False}).json()
    assert off["proposed"] is False and _jobs(tmp_path) == []

    client.put("/api/cron/weekly-review", json={"enabled": True})
    paused = client.put("/api/cron/weekly-review", json={"enabled": False}).json()
    job = find_proposal(_jobs(tmp_path))
    assert job is not None and not job.enabled
    assert job.disabled_by == "human", "a pause from the screen reads as the brake in `cron doctor`"
    assert paused["enabled"] is False


def test_the_destination_is_shown_by_host_only(client: TestClient, tmp_path: Path) -> None:
    """The webhook URL is a credential: whoever holds it can post into that channel."""
    from chimera.scheduler import Scheduler
    from chimera.scheduler.weekly_review import propose

    store = CronStore(tmp_path / "home" / "scheduler" / "jobs.json")
    propose(
        Scheduler(store), now=0.0, deliver_to="https://discord.com/api/webhooks/123/s3cr3t-token"
    )

    got = client.get("/api/cron/weekly-review").json()
    assert got["posts_to"] == "https://discord.com/…"
    assert "s3cr3t" not in json.dumps(got)



# --- the backend reads the shell's file the way the shell does --------------------------------


_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "shell_prefs_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_the_shared_cases_get_the_verdicts_serde_gives_them(
    case: dict[str, object], shell: tuple[Path, Path]
) -> None:
    """The shell's serde rejects the WHOLE file on one wrong-typed known key and runs on its
    defaults. Reading key by key here showed values as in force that the shell was ignoring. The
    same cases are asserted against serde in prefs.rs, so the two readers cannot drift apart."""
    prefs, _state = shell
    body = str(case["body"])
    assert (parse_prefs(body) is not None) is case["valid"]

    prefs.write_text(body, encoding="utf-8")
    got = read_shell_prefs()
    assert got["unreadable"] is (not case["valid"])
    expect = case.get("expect")
    if isinstance(expect, dict):
        switches = ("keep_in_tray", "call_attention", "quick_entry")
        assert {k: got[k] for k in switches} == {k: expect[k] for k in switches}
        assert got["quick_entry_chord"] == expect["quick_entry_chord"]
        assert got["sign_in_requested"] == expect["start_at_sign_in"]
    else:
        # What the shell is actually running on.
        assert (got["keep_in_tray"], got["call_attention"], got["quick_entry"]) == (False, True, False)


@pytest.mark.parametrize(
    "body", ['{"keep_in_tray": "yes"}', '{"quick_entry_chord": null}', '{"start_at_sign_in": "true"}']
)
def test_a_save_over_a_file_the_shell_refuses_is_a_409_and_writes_nothing(
    client: TestClient, shell: tuple[Path, Path], body: str
) -> None:
    prefs, _state = shell
    prefs.write_text(body, encoding="utf-8")

    refused = client.patch("/api/shell/prefs", json={"quick_entry": True})

    assert refused.status_code == 409
    assert "cannot read" in refused.json()["detail"]
    assert prefs.read_text(encoding="utf-8") == body


def test_two_saves_at_once_both_land(shell: tuple[Path, Path]) -> None:
    """Each save reads, changes one key and replaces the file. Interleaved without a lock, the
    second would write back the first one's old value; with one shared temporary name, one would
    replace the other's half-written file."""
    import threading

    prefs, _state = shell
    keys = ["keep_in_tray", "quick_entry"] * 10
    barrier = threading.Barrier(len(keys))
    errors: list[BaseException] = []

    def save(i: int, key: str) -> None:
        barrier.wait()
        try:
            write_shell_prefs({key: i % 4 < 2})
        except BaseException as exc:  # collected: a thread's exception does not fail the test
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(i, k)) for i, k in enumerate(keys)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    on_disk = json.loads(prefs.read_text(encoding="utf-8"))
    assert set(on_disk) == {"keep_in_tray", "quick_entry"}, "a save lost the other's key"
    assert [p.name for p in prefs.parent.iterdir() if p.name.endswith(".tmp")] == []


def test_a_replace_windows_refuses_for_a_moment_is_retried(
    shell: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import os as real_os

    import chimera.api.shell_prefs as module

    monkeypatch.setattr(module, "_DENIED_PAUSE", 0.0)
    real_replace = real_os.replace
    refusals = iter([True, True, False])

    def flaky(src: str, dst: str) -> None:
        if next(refusals):
            raise PermissionError(13, "Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(module.os, "replace", flaky)
    assert write_shell_prefs({"keep_in_tray": True})["keep_in_tray"] is True


def test_a_replace_that_stays_refused_is_a_503_and_leaves_no_temporary_file(
    client: TestClient, shell: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.api.shell_prefs as module

    prefs, _state = shell
    monkeypatch.setattr(module, "_DENIED_PAUSE", 0.0)

    def locked(_src: str, _dst: str) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(module.os, "replace", locked)
    busy = client.patch("/api/shell/prefs", json={"keep_in_tray": True})

    assert busy.status_code == 503
    assert "in use" in busy.json()["detail"]
    leftovers = [p.name for p in prefs.parent.iterdir() if p.name.startswith(prefs.name)]
    assert leftovers == [], "the temporary file was left behind"


def test_a_read_windows_refuses_for_a_moment_is_retried_and_one_that_stays_is_busy(
    shell: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A locked file is not a broken one: reading it as unreadable would show the shell's defaults
    for settings that are in force."""
    import chimera.api.shell_prefs as module

    prefs, _state = shell
    prefs.write_text('{"keep_in_tray": true}', encoding="utf-8")
    monkeypatch.setattr(module, "_DENIED_PAUSE", 0.0)
    real_read = Path.read_text
    refusals = iter([True, False])

    def flaky(self: Path, *a: object, **k: object) -> str:
        if self == prefs and next(refusals, False):
            raise PermissionError(13, "Access is denied")
        return real_read(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", flaky)
    assert read_shell_prefs()["keep_in_tray"] is True

    def locked(self: Path, *a: object, **k: object) -> str:
        if self == prefs:
            raise PermissionError(13, "Access is denied")
        return real_read(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", locked)
    with pytest.raises(ShellPrefsBusy):
        read_shell_prefs()


# --- no tool writes the shell's files -----------------------------------------------------------


class _Tool:
    name = "write_file"

    def __init__(self, workspace: Path, says_yes: bool) -> None:
        self.workspace = workspace
        self.asked: list[object] = []
        self.says_yes = says_yes

    def ask_outside(self, question: object) -> bool:
        self.asked.append(question)
        return self.says_yes


def test_no_file_tool_writes_the_shells_switches_even_with_a_yes(
    shell: tuple[Path, Path], tmp_path: Path
) -> None:
    """One key in that file asks the OS to start the app at sign-in. An approval card naming the
    file is not a question a person can be expected to read that way, so the file tools refuse it
    before asking, and also inside a workspace that happens to contain it."""
    from chimera.tools.workspace import PathEscapesWorkspaceError, resolve_for

    prefs, state = shell
    project = tmp_path / "project"
    project.mkdir()
    outside = _Tool(project, says_yes=True)
    for target in (prefs, state):
        for verb in ("write", "edit"):
            with pytest.raises(PathEscapesWorkspaceError, match="belongs to the desktop app"):
                resolve_for(outside, str(target), verb=verb)
    assert outside.asked == [], "the person was asked a question whose yes would be refused anyway"

    inside = _Tool(tmp_path, says_yes=True)
    with pytest.raises(PathEscapesWorkspaceError, match="belongs to the desktop app"):
        resolve_for(inside, "shell-prefs.json", verb="write")

    # Reading stays allowed, and a neighbouring file is untouched by the rule.
    assert resolve_for(inside, "shell-prefs.json", verb="read") == prefs.resolve()
    assert resolve_for(inside, "notes.txt", verb="write") == (tmp_path / "notes.txt").resolve()


def test_the_tool_guard_names_the_variables_the_shell_sets() -> None:
    from chimera.tools.workspace import SHELL_OWNED_ENV

    assert SHELL_OWNED_ENV == (PREFS_ENV, STATE_ENV)
