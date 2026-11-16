"""Audit P0-15 (2): a NEW home starts in ``observe``; an EXISTING one keeps ``off`` and is told.

The verdict is taken once and written to the home, because "new" is only true before the first boot
leaves files behind: a test that read the home twice without that file would call the same install new
and then existing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.governance import first_boot
from chimera.governance.ledger import TaintLedger
from chimera.governance.profile import governed_profile
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "fresh-home"
    monkeypatch.setenv("CHIMERA_HOME", str(target))
    monkeypatch.delenv("CHIMERA_GOVERNANCE", raising=False)
    monkeypatch.chdir(tmp_path)  # no project `.env` / `.chimera` leaks in
    first_boot.forget()
    get_settings.cache_clear()
    yield target
    get_settings.cache_clear()


def _boot() -> object:
    first_boot.forget()
    get_settings.cache_clear()
    return get_settings()


def _verdict(home: Path) -> dict[str, object]:
    return json.loads((home / first_boot.STATE_FILE).read_text(encoding="utf-8"))


def test_a_new_home_starts_in_observe_and_records_why(home: Path) -> None:
    assert _boot().governance_mode == "observe"
    assert _verdict(home)["default"] == "observe"
    assert _verdict(home)["reason"] == first_boot.NEW_INSTALL


def test_an_empty_existing_directory_is_still_new(home: Path) -> None:
    home.mkdir(parents=True)
    assert _boot().governance_mode == "observe"


# Conservative: ANY earlier entry makes the home existing, not just the files an audit would know.
LEFTOVERS = ["memory.db", "memory.json", ".env", "agent.json", "audit.jsonl", "history.db", "skills", "x"]


@pytest.mark.parametrize("leftover", LEFTOVERS)
def test_a_home_holding_anything_and_no_verdict_stays_off(home: Path, leftover: str) -> None:
    home.mkdir(parents=True)
    if leftover == "skills":
        (home / leftover).mkdir()
    else:
        (home / leftover).write_text("", encoding="utf-8")
    assert _boot().governance_mode == "off"
    # Nothing is written for an existing home: a read-only command must not leave a file behind.
    assert not (home / first_boot.STATE_FILE).exists()


def test_a_home_with_only_memory_db_is_existing(home: Path) -> None:
    home.mkdir(parents=True)
    (home / "memory.db").write_bytes(b"SQLite format 3\x00")
    assert _boot().governance_mode == "off"
    assert first_boot.notice_active(home) is True


def test_an_empty_governance_variable_counts_as_not_configured(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIMERA_GOVERNANCE", "")  # what a copied .env.example holds
    assert _boot().governance_mode == "observe"


def test_an_empty_governance_variable_on_an_existing_home_stays_off(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home.mkdir(parents=True)
    (home / "memory.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CHIMERA_GOVERNANCE", "")
    assert _boot().governance_mode in ("", "off")
    assert first_boot.notice_active(home) is True


def test_the_verdict_is_published_whole_and_leaves_no_temporary(home: Path) -> None:
    _boot()
    assert sorted(entry.name for entry in home.iterdir()) == [first_boot.STATE_FILE]
    assert json.loads((home / first_boot.STATE_FILE).read_text(encoding="utf-8"))["default"] == "observe"


def test_a_second_writer_yields_to_the_first(home: Path) -> None:
    home.mkdir(parents=True)
    first = first_boot.FirstBoot("observe", first_boot.NEW_INSTALL)
    assert first_boot._write_exclusive(home / first_boot.STATE_FILE, first) is True
    other = first_boot.FirstBoot("off", first_boot.EXISTING_INSTALL)
    assert first_boot._write_exclusive(home / first_boot.STATE_FILE, other) is True
    assert _verdict(home)["default"] == "observe"
    assert sorted(entry.name for entry in home.iterdir()) == [first_boot.STATE_FILE]


def test_an_existing_home_is_told_by_the_long_lived_surfaces_and_the_screen(
    home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    home.mkdir(parents=True)
    (home / "agent.json").write_text("{}", encoding="utf-8")
    settings = _boot()
    with caplog.at_level(logging.WARNING, logger="chimera.governance.first_boot"):
        assert first_boot.announce_existing_install(settings) is True
        assert first_boot.announce_existing_install(settings) is False  # once per process
    assert "observe" in caplog.text and "chimera report governance" in caplog.text
    assert first_boot.notice_active(home) is True


def test_get_settings_never_prints_the_notice(home: Path, caplog: pytest.LogCaptureFixture) -> None:
    """It runs in every command; a warning there landed in JSON output, `chimera logs` and scripts."""
    home.mkdir(parents=True)
    (home / "agent.json").write_text("{}", encoding="utf-8")
    with caplog.at_level(logging.DEBUG):
        _boot()
        _boot()
    assert "Governance is OFF" not in caplog.text


def test_a_cli_command_with_json_output_on_an_existing_home_prints_only_the_json(
    home: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    from typer.testing import CliRunner

    from chimera.cli.main import app

    home.mkdir(parents=True)
    (home / "agent.json").write_text("{}", encoding="utf-8")
    first_boot.forget()
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["skills", "doctor", "--json"])
    assert result.exit_code == 0, result.output
    json.loads(result.output)  # the notice, if it leaked in, would make this fail
    seen = capfd.readouterr()
    assert "Governance is OFF" not in result.output + seen.out + seen.err


def test_the_verdict_is_memoised_so_later_calls_touch_no_disk(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_boot.forget()
    first = first_boot.resolve(home)

    def boom(_home: Path) -> first_boot.FirstBoot:
        raise AssertionError("the second call read the disk")

    monkeypatch.setattr(first_boot, "_resolve", boom)
    assert first_boot.resolve(home) == first


def test_a_new_home_is_never_shown_the_notice(home: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="chimera.governance.first_boot"):
        _boot()
    assert caplog.text == ""
    assert first_boot.notice_active(home) is False


def test_choosing_a_mode_in_the_env_file_ends_the_notice(home: Path) -> None:
    home.mkdir(parents=True)
    (home / "agent.json").write_text("{}", encoding="utf-8")
    _boot()
    env = home / ".env"
    assert first_boot.notice_active(home, env_files=(env,)) is True
    env.write_text("export CHIMERA_GOVERNANCE=observe\n", encoding="utf-8")
    assert first_boot.notice_active(home, env_files=(env,)) is False


def test_an_explicit_mode_in_the_environment_wins_on_a_new_home(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIMERA_GOVERNANCE", "enforce")
    assert _boot().governance_mode == "enforce"
    # ...and the verdict is still taken, so unsetting the variable later does not make it "existing".
    assert _verdict(home)["reason"] == first_boot.NEW_INSTALL


def test_an_explicit_off_wins_on_a_new_home(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_GOVERNANCE", "off")
    assert _boot().governance_mode == "off"


def test_the_second_boot_is_stable_even_though_the_first_left_files_behind(home: Path) -> None:
    assert _boot().governance_mode == "observe"
    (home / "audit.jsonl").write_text("", encoding="utf-8")  # what the first run leaves behind
    (home / "history.db").write_text("", encoding="utf-8")
    assert _boot().governance_mode == "observe"
    assert first_boot.notice_active(home) is False


def test_a_recorded_off_verdict_is_kept_even_if_the_home_later_looks_empty(home: Path) -> None:
    home.mkdir(parents=True)
    (home / first_boot.STATE_FILE).write_text(
        '{"version": 1, "default": "off", "reason": "existing-install"}', encoding="utf-8"
    )
    assert _boot().governance_mode == "off"
    assert first_boot.notice_active(home) is True


def test_a_home_that_cannot_hold_the_verdict_stays_off_and_does_not_crash(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "mkdir", refuse)
    verdict = first_boot.resolve(home)
    assert (verdict.default, verdict.reason) == ("off", first_boot.UNWRITABLE)


def test_a_corrupt_verdict_is_taken_again_rather_than_trusted(home: Path) -> None:
    home.mkdir(parents=True)
    (home / first_boot.STATE_FILE).write_text("{not json", encoding="utf-8")
    # `open(..., "x")` finds the file and the exclusive write yields to it; the read-back then fails
    # and the freshly computed verdict is used for this boot, never an exception.
    assert first_boot.resolve(home).default in ("off", "observe")


# --- what the default actually does to a run (the real default, on a home the conftest does not touch)


class _Shell(Tool):
    name = "run_shell"
    description = "runs"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> str:
        self.calls.append(dict(kwargs))
        return "ran"


class _Writer(Tool):
    name = "write_file"
    description = "writes"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.calls = 0

    def run(self, **kwargs: Any) -> str:
        self.calls += 1
        return "wrote"


def test_a_new_install_refuses_the_hard_blocks_and_only_records_what_enforce_would_review(
    home: Path,
) -> None:
    """The default is ``observe`` on a new home, and `observe` is not "refuses nothing".

    A BLOCK (a fixed signature) is refused in every mode the kernel is installed in - the owner's
    hard blocks - and under ``off`` the kernel is not installed, so a new install now refuses
    `rm -rf /` where it used to run it. A REVIEW verdict reaches an approver that says yes: nothing is
    asked and nothing is refused, it is only recorded. (Known false positive of the signature
    `disk_destroy`, outside this change: `echo mkfs` is refused too.)
    """
    shell, writer = _Shell(), _Writer()
    base = ToolRegistry()
    base.register(shell)
    base.register(writer)
    settings = get_settings()
    assert settings.governance_mode == "observe"

    registry, approvals = governed_profile(base, settings=settings, home=home, surface="serve")

    blocked = registry.get("run_shell").run(command="rm -rf /")
    assert "BLOCKED" in blocked and shell.calls == [], "a hard block ran in a new install"
    assert approvals.refused, "the refusal was not recorded"

    tool = registry.get("write_file")
    for wrapper in (tool, getattr(tool, "inner", None)):
        ledger = getattr(wrapper, "ledger", None)
        if isinstance(ledger, TaintLedger):
            ledger.record_fetch("news-feed", content="the market moved")
            break
    granted_before = len(approvals.granted)
    out = tool.run(path="report.md", content="today's summary")
    assert out == "wrote" and writer.calls == 1, "observe refused something enforce would only review"
    assert len(approvals.granted) > granted_before, "observe recorded nothing for the review"


def test_an_existing_install_keeps_the_kernel_out_so_a_hard_block_is_not_installed(
    home: Path,
) -> None:
    home.mkdir(parents=True)
    (home / "memory.db").write_bytes(b"x")
    shell = _Shell()
    base = ToolRegistry()
    base.register(shell)
    settings = get_settings()
    assert settings.governance_mode == "off"
    registry, _ = governed_profile(base, settings=settings, home=home, surface="serve")
    assert registry is base


# --- the first boot writes the verdict BEFORE anything asks "is this a new install?" ----------------


def test_the_verdict_file_does_not_make_a_first_run_look_like_an_upgrade(home: Path) -> None:
    """`get_settings()` writes the verdict on the very first boot; the bot's pairing check comes after.

    Counting that file made every first `serve --discord` an "upgrade" and started the bot OPEN.
    """
    from chimera.server.allowlist import home_is_empty, is_new_install

    settings = get_settings()  # the first boot: writes governance_default.json
    assert (home / first_boot.STATE_FILE).is_file()
    assert home_is_empty(settings) is True
    for platform in ("discord", "telegram", "whatsapp"):
        assert is_new_install(settings, platform) is True, f"{platform} would start open"


def test_a_leftover_temporary_does_not_make_a_home_existing_either(home: Path) -> None:
    from chimera.server.allowlist import home_is_empty

    home.mkdir(parents=True)
    (home / f"{first_boot.STATE_FILE}.123.456.abc.tmp").write_text("{", encoding="utf-8")
    assert home_is_empty(get_settings()) is True


def test_a_real_file_still_makes_the_home_not_empty(home: Path) -> None:
    from chimera.server.allowlist import home_is_empty

    home.mkdir(parents=True)
    (home / "memory.db").write_bytes(b"x")
    assert home_is_empty(get_settings()) is False


def test_an_existing_home_with_an_empty_variable_is_not_switched_by_the_desktop(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from chimera.cli.main import _kernel_observes_unless_told_otherwise

    home.mkdir(parents=True)
    (home / "agent.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CHIMERA_GOVERNANCE", "")
    get_settings.cache_clear()
    _kernel_observes_unless_told_otherwise()
    assert os.environ.get("CHIMERA_GOVERNANCE", "") == ""
    assert get_settings().governance_mode in ("", "off")


def test_a_new_home_with_an_empty_variable_is_observe_on_the_desktop_too(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.cli.main import _kernel_observes_unless_told_otherwise

    monkeypatch.setenv("CHIMERA_GOVERNANCE", "")
    get_settings.cache_clear()
    _kernel_observes_unless_told_otherwise()
    assert get_settings().governance_mode == "observe"


# --- atomic and race-proof publication --------------------------------------------------------------


def test_two_threads_publishing_at_once_leave_one_whole_verdict_and_no_temporary(home: Path) -> None:
    import threading

    home.mkdir(parents=True)
    path = home / first_boot.STATE_FILE
    results: list[bool] = []
    barrier = threading.Barrier(8)

    def write() -> None:
        barrier.wait()
        results.append(first_boot._write_exclusive(path, first_boot.FirstBoot("observe", first_boot.NEW_INSTALL)))

    threads = [threading.Thread(target=write) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [True] * 8
    assert json.loads(path.read_text(encoding="utf-8"))["default"] == "observe"
    assert sorted(entry.name for entry in home.iterdir()) == [first_boot.STATE_FILE]


def test_without_hard_links_the_verdict_is_still_published(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    def no_links(*_args: object, **_kwargs: object) -> None:
        raise OSError("links not supported")

    monkeypatch.setattr(os, "link", no_links)
    home.mkdir(parents=True)
    assert first_boot._write_exclusive(home / first_boot.STATE_FILE, first_boot.FirstBoot("observe", "x")) is True
    assert _verdict(home)["default"] == "observe"
    assert sorted(entry.name for entry in home.iterdir()) == [first_boot.STATE_FILE]


def test_an_empty_or_partial_verdict_file_means_no_verdict_and_never_an_error(home: Path) -> None:
    home.mkdir(parents=True)
    for partial in ("", '{"default": "obs'):
        (home / first_boot.STATE_FILE).write_text(partial, encoding="utf-8")
        assert first_boot.resolve(home).default == "observe"  # a new home: the file is ignored, not an entry
