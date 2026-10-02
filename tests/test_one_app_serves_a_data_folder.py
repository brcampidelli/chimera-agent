"""One Chimera Desktop server per data folder.

Found reading the code on 2026-09-30 (R12 of the review of several conversations at once). Nothing
stopped a second copy of the app — a second click on the icon, or `chimera app` in a terminal —
from serving the same `CHIMERA_HOME`. Each server keeps in memory the turns it runs, which folder
each is editing, the undo offers and the live frames, so two of them on one folder each believed
it was alone: the folder lock that keeps two conversations from editing one folder at once (R9)
held only within a server, and Stop in one window could not reach a turn the other was running.

Now the server claims the data folder before it builds anything, with an OS lock that dies with
the process. A second server on the same folder says where the first one is and exits with code 3.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from chimera.core.instance import claim_home, running_url


def test_a_second_claim_on_the_same_folder_is_refused_until_the_first_lets_go(tmp_path: Path) -> None:
    first = claim_home(tmp_path)
    assert first is not None

    assert claim_home(tmp_path) is None, "two servers claimed one data folder"
    first.release()
    again = claim_home(tmp_path)
    assert again is not None, "a released folder stayed claimed"
    again.release()


def test_another_folder_is_not_affected(tmp_path: Path) -> None:
    a = claim_home(tmp_path / "a")
    b = claim_home(tmp_path / "b")
    assert a is not None and b is not None
    a.release()
    b.release()


def test_the_claim_says_where_the_running_server_is_and_forgets_it_on_release(tmp_path: Path) -> None:
    held = claim_home(tmp_path)
    assert held is not None
    held.announce("http://127.0.0.1:8765")

    assert running_url(tmp_path) == "http://127.0.0.1:8765"
    held.release()
    assert running_url(tmp_path) is None, "a stopped server was still named as running"


def test_another_process_is_refused_too(tmp_path: Path) -> None:
    """The case that matters: the lock is the OS's, so it holds across processes and a crashed
    server leaves nothing behind that would lock the folder forever."""
    held = claim_home(tmp_path)
    assert held is not None
    code = (
        "import sys; from pathlib import Path; from chimera.core.instance import claim_home; "
        f"sys.exit(0 if claim_home(Path(r'{tmp_path}')) is None else 1)"
    )
    try:
        refused = subprocess.run([sys.executable, "-c", code], timeout=60)
        assert refused.returncode == 0, "another process claimed a folder this one holds"
    finally:
        held.release()
    taken = subprocess.run([sys.executable, "-c", code.replace("is None else 1", "is not None else 1")], timeout=60)
    assert taken.returncode == 0, "the folder stayed locked after its holder let go"


def test_the_app_command_refuses_a_held_folder_before_building_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.api
    from chimera.cli.main import app
    from chimera.config import get_settings

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()

    def _built(*_a: object, **_kw: object) -> None:
        raise AssertionError("the app was built on a folder another server holds")

    monkeypatch.setattr(chimera.api, "build_api_app", _built)
    held = claim_home(home)
    assert held is not None
    held.announce("http://127.0.0.1:9999")
    try:
        result = CliRunner().invoke(app, ["app", "--no-open", "--port", "0"])
    finally:
        held.release()
        get_settings.cache_clear()

    assert result.exit_code == 3, result.output
    assert "already running" in result.output and "http://127.0.0.1:9999" in result.output


def test_a_serving_app_names_itself_and_lets_go_when_it_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.cli.main import app
    from chimera.config import get_settings

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    seen: dict[str, object] = {}

    def _serve(_self: object, **_kw: object) -> None:
        seen["url"] = running_url(home)
        seen["claimable"] = claim_home(home) is not None

    monkeypatch.setattr("uvicorn.Server.run", _serve)
    monkeypatch.setattr("chimera.cli.main._start_cron_daemon", lambda *a, **k: None)
    result = CliRunner().invoke(app, ["app", "--no-open", "--no-memory", "--no-cron", "--port", "0"])
    get_settings.cache_clear()

    assert result.exit_code == 0, result.output
    assert str(seen["url"]).startswith("http://127.0.0.1:"), "a serving app did not say where it is"
    assert seen["claimable"] is False, "the folder was not held while the app served"
    assert running_url(home) is None and claim_home(home) is not None, "the app kept the folder after it stopped"
