"""``chimera service start`` brings up the installed app's backend, and nothing else (S1, D9).

The frozen backend records ``desktop-install.json`` — ``{exe, cwd, env, version, port}`` — and
``service start`` runs that executable from that folder with those four variables added. Each rule
here is one a regression would break without any error:

* the record carries only the four shell paths: a key or a token in it would be the owner's
  credentials in a second file, one the bridge's secret list has to know about;
* the spawn uses the record's ``cwd``: a frozen backend reads only the working directory's ``.env``,
  so another folder boots it keyless and "start" looks like it worked;
* an edited record is refused, loudly: a file anyone could have written must not choose the program;
* without a record, the interpreter's own Chimera starts, as before the record existed.

No real process is started: ``subprocess.Popen`` is replaced by a spy that records what it was
asked to run and reports an immediate exit, which ends ``start`` at once.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.cli import service_cmd
from chimera.config import get_settings
from chimera.core.desktop_install import (
    ALLOWED_ENV,
    BACKEND_NAME,
    INSTALL_FILE,
    InstallRecord,
    InvalidInstallRecord,
    current_record,
    install_path,
    load,
    validate,
    write_install,
)
from chimera.core.service_record import DEFAULT_PORTS

SECRET = "sk-or-v1-this-must-never-reach-the-install-record"


@pytest.fixture
def folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    svc, install, data = tmp_path / "svc", tmp_path / "Program Files" / "Chimera", tmp_path / "appdata" / "data"
    for folder in (svc, install, data):
        folder.mkdir(parents=True)
    monkeypatch.setenv("CHIMERA_SERVICE_DIR", str(svc))
    monkeypatch.delenv("CHIMERA_SERVICE_CHANNEL", raising=False)
    exe = install / BACKEND_NAME
    # A script where the executable would be: the spy never runs it, the validator only needs a file.
    exe.write_text("#!/bin/sh\necho 'a fake launcher'\n", encoding="utf-8")
    return {"svc": svc, "install": install, "data": data, "exe": exe}


class PopenSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> Any:
        self.calls.append((list(command), kwargs))

        class Exited:
            pid = 4242

            def poll(self) -> int:
                return 1  # "exited with code 1": start_service stops waiting at once

            def kill(self) -> None:
                raise AssertionError("an exited child is never killed")

        return Exited()


@pytest.fixture
def popen(monkeypatch: pytest.MonkeyPatch) -> PopenSpy:
    spy = PopenSpy()
    monkeypatch.setattr(service_cmd.subprocess, "Popen", spy)
    return spy


def _record(folders: dict[str, Path], **override: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "exe": str(folders["exe"]),
        "cwd": str(folders["install"]),
        "env": {
            "CHIMERA_HOME": str(folders["data"]),
            "CHIMERA_WORKSPACE": str(folders["data"].parent / "workspace"),
            "CHIMERA_SHELL_PREFS": str(folders["data"].parent / "prefs.json"),
            "CHIMERA_SHELL_STATE": str(folders["data"].parent / "state.json"),
        },
        "version": "0.67.0",
        "port": 51234,
    }
    data.update(override)
    return data


def _write_raw(data: Any) -> None:
    install_path().parent.mkdir(parents=True, exist_ok=True)
    install_path().write_text(json.dumps(data), encoding="utf-8")


def _start() -> int:
    return service_cmd.start_service(port=None, workspace=None, timeout=5.0, as_json=False)


# ---- the frozen backend writes it -----------------------------------------------------------------


def _serve_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, args: list[str], *, frozen: bool) -> Any:
    from chimera.cli.main import app

    cwd = tmp_path / "install-dir"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_SERVICE_DIR", str(tmp_path / "svc"))
    monkeypatch.setenv("CHIMERA_SERVICE_CHANNEL", "dev")
    monkeypatch.setenv("CHIMERA_BRIDGE_DIR", str(tmp_path / "svc"))
    monkeypatch.setenv("CHIMERA_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("CHIMERA_SHELL_PREFS", str(tmp_path / "prefs.json"))
    monkeypatch.setenv("CHIMERA_SHELL_STATE", str(tmp_path / "state.json"))
    # What a real install's environment also holds, and the record must not.
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    monkeypatch.setenv("CHIMERA_SHELL_REQUEST", str(tmp_path / "request.json"))
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "0")
    if frozen:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    get_settings.cache_clear()
    seen: dict[str, Any] = {}

    def serving(_self: object, **_kw: object) -> None:
        path = tmp_path / "svc" / "desktop-install.json"
        seen["raw"] = path.read_text(encoding="utf-8") if path.exists() else None

    monkeypatch.setattr("uvicorn.Server.run", serving)
    monkeypatch.setattr("chimera.cli.commands.serve._start_cron_daemon", lambda *a, **k: None)
    try:
        result = CliRunner().invoke(app, args)
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    seen["cwd"] = cwd
    return seen


def test_the_frozen_backend_records_only_the_allowed_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve_app(monkeypatch, tmp_path, ["app", "--no-open", "--no-memory", "--no-cron", "--port", "0"], frozen=True)

    raw = seen["raw"]
    assert raw is not None, "the frozen backend wrote no install record"
    data = json.loads(raw)
    assert set(data) == {"exe", "cwd", "env", "version", "port"}
    assert set(data["env"]) == set(ALLOWED_ENV), data["env"]
    assert SECRET not in raw and "CHIMERA_SHELL_REQUEST" not in raw and "OPENROUTER" not in raw
    assert data["cwd"] == str(seen["cwd"].resolve()), "the record lost the folder the .env is read from"
    assert data["exe"] == str(Path(sys.executable).resolve())
    assert isinstance(data["port"], int) and data["port"] > 0, "the record names the bound port, not 0"
    assert isinstance(data["version"], str) and data["version"]


def test_a_checkout_writes_no_install_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve_app(monkeypatch, tmp_path, ["app", "--no-open", "--no-memory", "--no-cron", "--port", "0"], frozen=False)

    assert seen["raw"] is None


def test_standalone_writes_no_install_record_even_frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve_app(
        monkeypatch, tmp_path, ["app", "--standalone", "--no-open", "--no-memory", "--no-cron", "--port", "0"], frozen=True
    )

    assert seen["raw"] is None


def test_the_writer_refuses_a_variable_outside_the_list(folders: dict[str, Path]) -> None:
    record = InstallRecord(exe=str(folders["exe"]), cwd=str(folders["install"]), version="1", env={"OPENROUTER_API_KEY": SECRET})

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        write_install(record)
    assert not install_path().exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits; Windows has no 0600 to read back")
def test_the_install_record_is_0600(folders: dict[str, Path]) -> None:
    write_install(InstallRecord(exe=str(folders["exe"]), cwd=str(folders["install"]), version="1"))

    assert stat.S_IMODE(install_path().stat().st_mode) == 0o600


# ---- service start uses it ------------------------------------------------------------------------


def test_start_runs_the_installed_backend_from_its_folder_with_its_env(
    folders: dict[str, Path], popen: PopenSpy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOMETHING_THE_TERMINAL_HAD", "kept")
    monkeypatch.setenv("CHIMERA_HOME", str(folders["svc"] / "the-terminals-own-home"))
    record = _record(folders)
    _write_raw(record)

    assert _start() == 1  # the spy "exited": what matters is what it was asked to run

    assert len(popen.calls) == 1
    command, kwargs = popen.calls[0]
    assert command == [str(folders["exe"]), "service", "run", "--port", "51234"]
    assert kwargs["cwd"] == str(folders["install"]), "spawned away from the install's .env"
    env = kwargs["env"]
    for name, value in record["env"].items():
        assert env[name] == value, f"{name} is not the install's"
    assert env["SOMETHING_THE_TERMINAL_HAD"] == "kept", "the current environment was dropped"
    assert env["CHIMERA_SERVICE_CHANNEL"] == "stable"
    assert (folders["data"] / "logs" / "service.log").exists(), "the log is not in the app's data folder"


def test_without_a_record_start_runs_this_interpreter(folders: dict[str, Path], popen: PopenSpy) -> None:
    assert not install_path().exists()

    _start()

    command, kwargs = popen.calls[0]
    assert command == [sys.executable, "-m", "chimera.cli.main", "service", "run", "--port", str(DEFAULT_PORTS["dev"])]
    assert kwargs["cwd"] == os.getcwd()
    assert kwargs["env"]["CHIMERA_SERVICE_CHANNEL"] == "dev"


def test_the_dev_channel_skips_the_record(
    folders: dict[str, Path], popen: PopenSpy, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_raw(_record(folders, exe=str(folders["install"] / "not-a-backend")))  # would be refused
    monkeypatch.setenv("CHIMERA_SERVICE_CHANNEL", "dev")

    _start()

    assert popen.calls[0][0][:3] == [sys.executable, "-m", "chimera.cli.main"]


def _refused(folders: dict[str, Path], popen: PopenSpy, capsys: pytest.CaptureFixture[str], data: Any) -> str:
    _write_raw(data)
    assert _start() == 1
    assert popen.calls == [], "an untrusted install record still launched something"
    out = capsys.readouterr().out
    assert "Not starting" in out, out
    return " ".join(out.split())


def test_a_record_naming_a_missing_exe_is_refused(
    folders: dict[str, Path], popen: PopenSpy, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _refused(folders, popen, capsys, _record(folders, exe=str(folders["install"] / "gone" / BACKEND_NAME)))
    assert "does not exist" in out


@pytest.mark.parametrize("name", ["python.exe", "python3", "sh", "chimera-backend-evil.exe", "cmd.exe"])
def test_a_record_naming_another_program_is_refused(
    folders: dict[str, Path], popen: PopenSpy, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    other = folders["install"] / name
    other.write_text("x", encoding="utf-8")

    out = _refused(folders, popen, capsys, _record(folders, exe=str(other)))
    assert "not a chimera-backend" in out


def test_a_record_naming_a_directory_as_exe_is_refused(
    folders: dict[str, Path], popen: PopenSpy, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = folders["install"] / "sub" / BACKEND_NAME
    folder.mkdir(parents=True)

    _refused(folders, popen, capsys, _record(folders, exe=str(folder)))


def test_a_relative_exe_is_refused(folders: dict[str, Path], popen: PopenSpy, capsys: pytest.CaptureFixture[str]) -> None:
    out = _refused(folders, popen, capsys, _record(folders, exe=BACKEND_NAME))
    assert "relative" in out


@pytest.mark.parametrize("cwd", ["missing", "a-file", "relative"])
def test_a_record_with_an_invalid_cwd_is_refused(
    folders: dict[str, Path], popen: PopenSpy, capsys: pytest.CaptureFixture[str], cwd: str
) -> None:
    target = {
        "missing": str(folders["install"] / "nope"),
        "a-file": str(folders["exe"]),
        "relative": "Chimera",
    }[cwd]

    out = _refused(folders, popen, capsys, _record(folders, cwd=target))
    assert "not a directory" in out


@pytest.mark.parametrize("version", [None, 67, "", ["0.67.0"]])
def test_a_record_without_a_version_string_is_refused(
    folders: dict[str, Path], popen: PopenSpy, capsys: pytest.CaptureFixture[str], version: Any
) -> None:
    out = _refused(folders, popen, capsys, _record(folders, version=version))
    assert "version" in out


@pytest.mark.parametrize("name", ["PYTHONPATH", "LD_PRELOAD", "PATH", "OPENROUTER_API_KEY"])
def test_a_record_setting_another_variable_is_refused(
    folders: dict[str, Path], popen: PopenSpy, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    data = _record(folders)
    data["env"][name] = "x"

    out = _refused(folders, popen, capsys, data)
    assert name in out


@pytest.mark.parametrize("junk", ["not json", "[1, 2]"])
def test_a_record_that_is_not_an_object_is_refused(
    folders: dict[str, Path], popen: PopenSpy, capsys: pytest.CaptureFixture[str], junk: str
) -> None:
    install_path().write_text(junk, encoding="utf-8")

    assert _start() == 1 and popen.calls == []


@pytest.mark.skipif(sys.platform == "win32", reason="a symlink needs a privilege Windows runners lack")
def test_a_link_named_like_the_backend_is_judged_by_what_it_points_at(
    folders: dict[str, Path], popen: PopenSpy, capsys: pytest.CaptureFixture[str]
) -> None:
    shell = folders["install"] / "sh"
    shell.write_text("x", encoding="utf-8")
    link = folders["install"] / "linked" / BACKEND_NAME
    link.parent.mkdir()
    link.symlink_to(shell)

    _refused(folders, popen, capsys, _record(folders, exe=str(link)))


def test_status_and_stop_look_where_start_published(folders: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    assert service_cmd.client_channel() == "dev"
    _write_raw(_record(folders))
    assert service_cmd.client_channel() == "stable"
    monkeypatch.setenv("CHIMERA_SERVICE_CHANNEL", "dev")
    assert service_cmd.client_channel() == "dev"


# ---- the bridge -----------------------------------------------------------------------------------


def test_the_bridge_refuses_the_install_record_as_a_file(folders: dict[str, Path]) -> None:
    from chimera.api.bridge_routes import is_secret_file

    write_install(InstallRecord(exe=str(folders["exe"]), cwd=str(folders["install"]), version="1"))

    assert is_secret_file(str(install_path()))
    assert is_secret_file("~/.chimera/desktop-install.json") and is_secret_file("DESKTOP-INSTALL.JSON")


def test_under_the_shell_the_record_names_the_install_folder_wherever_the_process_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect: the record took ``Path.cwd()``, so an app opened from another folder made the
    background service start there too, keyless. The shell names the install folder; the record
    follows that, not the working directory."""
    install, elsewhere = tmp_path / "Chimera", tmp_path / "somewhere-else"
    install.mkdir()
    elsewhere.mkdir()
    monkeypatch.setenv("CHIMERA_TAURI_SIDECAR", "1")
    monkeypatch.setenv("CHIMERA_INSTALL_DIR", str(install))
    monkeypatch.chdir(elsewhere)

    assert current_record(1234).cwd == str(install.resolve())


def test_without_the_shell_naming_a_folder_the_record_falls_back_to_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    named = tmp_path / "named"
    named.mkdir()
    monkeypatch.chdir(tmp_path)
    # Not launched by the shell: the variable is ignored.
    monkeypatch.delenv("CHIMERA_TAURI_SIDECAR", raising=False)
    monkeypatch.setenv("CHIMERA_INSTALL_DIR", str(named))
    assert current_record(1).cwd == str(tmp_path.resolve())
    # Launched by the shell but naming a folder that does not exist: the cwd, never a bad path.
    monkeypatch.setenv("CHIMERA_TAURI_SIDECAR", "1")
    monkeypatch.setenv("CHIMERA_INSTALL_DIR", str(tmp_path / "gone"))
    assert current_record(1).cwd == str(tmp_path.resolve())


def test_a_record_naming_a_folder_that_no_longer_exists_is_still_refused(folders: dict[str, Path]) -> None:
    gone = folders["install"] / "removed"
    gone.mkdir()
    write_install(validate(_record(folders, cwd=str(gone))), folders["svc"] / INSTALL_FILE)
    gone.rmdir()
    with pytest.raises(InvalidInstallRecord, match="not a directory"):
        load(folders["svc"] / INSTALL_FILE)
