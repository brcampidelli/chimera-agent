"""Chimera's own `.env` and data folder are known by what they ARE, not by how a path spells them.

The adversarial review of 2026-10-04 reached both through spellings the checks compared as text or
as `Path` objects, which Windows opens as the same file:

* ``.env ``, ``.env.``, ``.env::$DATA``, ``ENV~1`` passed the bridge's credential-file filter and
  read and wrote the real ``.env`` at the OPERATE tier (pre-existing, in 0.64.3);
* ``\\\\?\\<home>``, ``\\\\localhost\\C$\\…``, ``\\\\?\\UNC\\…`` kept their prefix through
  ``Path.resolve`` and compared unequal to the data folder they name, so a ``files.write`` answered an
  ordinary approval at the operate tier — refuting the data-folder commit before this one.

Held here: the filter matches each name as Windows opens it and the name a path resolves to; every
device or network spelling is refused by the bridge before any comparison; the data folder and
Chimera's own ``.env`` are compared by file identity (`chimera/core/own_files.py`); and the app's own
file routes refuse both for every caller — while another project's ``.env`` stays ordinary work.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.bridge_routes import is_secret_file
from chimera.core.own_files import is_device_or_unc, is_own_env, normal_name, same_file, within
from tests.test_the_desktop_bridge_reaches_only_its_table import FAKE_KEY, _app, _ask, _call

BS = "\\"
windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows path spellings")
ENV_TEXT = f"OPENROUTER_API_KEY={FAKE_KEY}\nCHIMERA_DEFAULT_MODEL=owner/model\n"


def test_a_name_is_matched_as_windows_opens_it() -> None:
    for name in (".env", ".env ", ".env.", ".env::$DATA", ".env. ", "dir/.env...", ".ENV"):
        assert is_secret_file(name), name
    assert normal_name(".env::$DATA") == ".env"
    assert normal_name(".env. .") == ".env"
    for name in ("environment.md", "src/env.ts", "notes.txt"):
        assert not is_secret_file(name), name


def test_every_device_or_network_spelling_is_recognised() -> None:
    for text in (
        BS * 2 + "?" + BS + "C:" + BS + "x",
        BS * 2 + "." + BS + "C:" + BS + "x",
        BS * 2 + "localhost" + BS + "C$" + BS + "x",
        BS * 2 + "?" + BS + "UNC" + BS + "localhost" + BS + "C$",
        "//server/share/x",
        " " + BS * 2 + "?" + BS + "C:",
    ):
        assert is_device_or_unc(text), text
    for text in ("C:" + BS + "x", "/home/x", "relative/x", BS + "rooted"):
        assert not is_device_or_unc(text), text


@windows
def test_identity_sees_through_a_device_prefix(tmp_path: Path) -> None:
    home = (tmp_path / "home").resolve()
    (home / "approvals").mkdir(parents=True)
    spelled = Path(BS * 2 + "?" + BS + str(home))
    assert spelled.resolve() != home, "the probe: resolve keeps the prefix"
    assert within(spelled / "approvals" / "q.answer.json", home)
    assert same_file(spelled, home)


@windows
@pytest.mark.parametrize("name", [".env ", ".env.", ".env::$DATA", ".env. ", ".ENV", "ENV~1"])
def test_no_spelling_of_env_is_read_or_written_through_the_bridge(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)  # operate tier; the default workspace is tmp_path
    env = tmp_path / ".env"
    env.write_text(ENV_TEXT, encoding="utf-8")
    if name == "ENV~1" and not (tmp_path / name).exists():
        pytest.skip("this volume keeps no 8.3 short names")
    with TestClient(app) as client:
        read = _call(client, app, "files.read", params={"path": name})
        wrote = _call(
            client,
            app,
            "files.write",
            body={"path": name, "content": "CHIMERA_REACH=workspace_shell\n"},
        )
    assert (read.status_code, wrote.status_code) == (403, 403), (read.text, wrote.text)
    assert FAKE_KEY not in read.text
    assert env.read_text(encoding="utf-8") == ENV_TEXT


def _device_spellings(path: Path) -> list[str]:
    text = str(path.resolve())
    drive, rest = text[0], text[2:]
    return [
        BS * 2 + "?" + BS + text,
        BS * 2 + "localhost" + BS + drive + "$" + rest,
        BS * 2 + "127.0.0.1" + BS + drive + "$" + rest,
        BS * 2 + "?" + BS + "UNC" + BS + "localhost" + BS + drive + "$" + rest,
        "//localhost/" + drive + "$" + rest.replace(BS, "/"),
    ]


@windows
def test_no_device_or_network_spelling_reaches_the_approval_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)  # operate: no switch that allows answering
    home = tmp_path / "home"
    directory = _ask(home)
    with TestClient(app) as client:
        for spelled in _device_spellings(home):
            as_workspace = _call(
                client,
                app,
                "files.write",
                body={
                    "workspace": spelled,
                    "path": "approvals/q1.answer.json",
                    "content": '{"approved": true}',
                },
            )
            as_path = _call(
                client,
                app,
                "files.write",
                body={"path": spelled + BS + "approvals" + BS + "q1.answer.json", "content": "{}"},
            )
            in_a_batch = _call(
                client,
                app,
                "agents.batch",
                body={"tasks": [{"task": "x", "workspace": spelled}]},
            )
            assert (as_workspace.status_code, as_path.status_code, in_a_batch.status_code) == (
                403,
                403,
                403,
            ), spelled
    assert not (directory / "q1.answer.json").exists()


def test_a_device_spelling_is_refused_on_any_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused by its shape, before any comparison — so the rule holds where `\\\\?\\` means nothing."""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        for text in ("//server/share/proj", BS * 2 + "?" + BS + "C:" + BS + "proj"):
            got = _call(client, app, "files.read", params={"path": "a.txt", "workspace": text})
            assert got.status_code == 403, text
            assert "device and network paths" in got.json()["detail"]


def _owner_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    return _app(tmp_path, monkeypatch)


def test_the_apps_file_routes_refuse_chimeras_own_env_and_data_for_every_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not only the bridge: the owner's own file viewer cannot open Chimera's `.env` (the Settings
    screen masks those keys) nor write into its data folder — while another project's `.env` is
    ordinary work and opens as before."""
    app = _owner_app(tmp_path, monkeypatch)
    env = tmp_path / ".env"
    env.write_text(ENV_TEXT, encoding="utf-8")
    home = tmp_path / "home"
    directory = _ask(home)
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".env.local").write_text("NEXT_PUBLIC_X=1\n", encoding="utf-8")
    with TestClient(app) as client:
        read_env = client.get("/api/fs/file", params={"path": ".env"})
        write_env = client.put("/api/fs/file", json={"path": ".env", "content": "X=1\n"})
        write_queue = client.put(
            "/api/fs/file", json={"path": "home/approvals/q1.answer.json", "content": "{}"}
        )
        other = client.get(
            "/api/fs/file", params={"path": ".env.local", "workspace": str(project)}
        )
    assert read_env.status_code == 400 and FAKE_KEY not in read_env.text
    assert write_env.status_code == 400
    assert write_queue.status_code == 400
    assert env.read_text(encoding="utf-8") == ENV_TEXT
    assert not (directory / "q1.answer.json").exists()
    assert other.status_code == 200 and other.json()["content"] == "NEXT_PUBLIC_X=1\n"


def test_chimeras_own_env_is_known_before_it_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert is_own_env(tmp_path / ".env")
    assert is_own_env(tmp_path / ".env.")
    other = tmp_path / "proj"
    other.mkdir()
    assert not is_own_env(other / ".env")
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
    assert is_own_env(Path(os.path.join(str(tmp_path), "sub", "..", ".env")))
    assert not is_own_env(other / ".env")
