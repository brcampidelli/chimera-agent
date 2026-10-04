"""The docker sandbox's network has a row, and every screen says what a command can actually reach.

`CHIMERA_SANDBOX_NETWORK` (`none` | `bridge`) was read by `get_sandbox` and reachable only by editing
`.env`: `PATCH /api/config` refused the key and `GET /api/config` did not report it. Study 29, P5.4.

Exposing it is the smaller half. The setting means something only inside a container that answered;
on Windows the default `auto` resolves to the host, where nothing fences the network at all, and a
row reading "network: none" there would be a fence that does not exist. So the server also says what
a command can reach HERE (`GET /api/governance/sandbox` -> `network`), and the screen shows the
switch only where it holds.

And the frozen desktop build has no interpreter of its own, so `execute_code` runs whatever Python
PATH holds — or none. `doctor` now names it, so "the code failed to run" stops reading like a model
that wrote bad code.

Free: no model call, no network, no container started.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from chimera.api.config_api import (
    APPLIES_WHEN,
    COMMANDS_NOW,
    doctor,
    is_editable,
    patch_config,
    read_config,
)
from chimera.config import Settings, get_settings
from chimera.sandbox import DockerSandbox, get_sandbox, sandbox_network
from chimera.tools import code as code_mod
from chimera.tools.code import host_python, host_python_report, python_command

# --- the row may write it, and only the two values the factory understands ---------------------


def test_the_screen_may_write_it() -> None:
    assert is_editable("CHIMERA_SANDBOX_NETWORK")


@pytest.mark.parametrize("value", ["none", "bridge", "BRIDGE", " none "])
def test_the_two_values_the_factory_reads_are_saved(
    value: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # patch_config writes os.environ for real; own the name first so the teardown restores it.
    monkeypatch.setenv("CHIMERA_SANDBOX_NETWORK", "none")
    env = tmp_path / ".env"

    assert patch_config({"CHIMERA_SANDBOX_NETWORK": value}, env_path=env) == {
        "updated": ["CHIMERA_SANDBOX_NETWORK"]
    }
    # Read back the way the app reads it, not as raw text: since 2026-10-04 the writer quotes a
    # value a bare line would change (`key_vault.encode_env_value`), so " none " is stored quoted
    # and comes back with its spaces — which the factory strips, as it did before.
    from dotenv import dotenv_values

    assert dotenv_values(env)["CHIMERA_SANDBOX_NETWORK"] == value
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "value",
    [
        # Docker's own value, and the one it would be natural to try: the container shares this
        # machine's network stack, which is no boundary at all.
        "host",
        "container:other",
        "bridge,none",
        "",
        "open",
    ],
)
def test_anything_else_is_refused_and_nothing_is_written(
    value: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CHIMERA_SANDBOX_NETWORK", "none")
    env = tmp_path / ".env"

    with pytest.raises(ValueError, match="CHIMERA_SANDBOX_NETWORK must be none or bridge"):
        patch_config({"CHIMERA_SANDBOX_NETWORK": value}, env_path=env)

    assert not env.exists(), "a refused value still reached .env"
    assert os.environ["CHIMERA_SANDBOX_NETWORK"] == "none", "a refused value reached the process"


def test_a_refused_value_takes_the_rest_of_the_patch_down_with_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The check runs before any write, so a batch carrying a bad value writes none of its keys."""
    monkeypatch.setenv("CHIMERA_SANDBOX_NETWORK", "none")
    monkeypatch.setenv("CHIMERA_SANDBOX", "auto")
    env = tmp_path / ".env"

    with pytest.raises(ValueError):
        patch_config({"CHIMERA_SANDBOX": "docker", "CHIMERA_SANDBOX_NETWORK": "host"}, env_path=env)

    assert not env.exists()


# --- the default stays closed, and the screen reports what the factory reads ---------------------


def test_the_default_is_no_network(tmp_path: Path) -> None:
    settings = Settings(CHIMERA_HOME=str(tmp_path))  # type: ignore[arg-type]

    assert read_config(settings)["sandbox"]["network"] == "none"
    sandbox = get_sandbox(Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_SANDBOX="docker"))  # type: ignore[arg-type]
    assert isinstance(sandbox, DockerSandbox)
    assert sandbox.network is False


def test_a_hand_edited_value_is_shown_as_the_factory_reads_it(tmp_path: Path) -> None:
    """`.env` is a file people edit. Whatever is in it, the row shows what the container will get."""
    odd = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_SANDBOX="docker", CHIMERA_SANDBOX_NETWORK="host")  # type: ignore[arg-type]

    assert read_config(odd)["sandbox"]["network"] == "none"
    built = get_sandbox(odd)
    assert isinstance(built, DockerSandbox) and built.network is False


def test_bridge_reaches_the_container_the_tools_are_given(tmp_path: Path) -> None:
    on = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_SANDBOX="docker", CHIMERA_SANDBOX_NETWORK="bridge")  # type: ignore[arg-type]

    assert read_config(on)["sandbox"]["network"] == "bridge"
    assert sandbox_network(on) == "bridge"
    built = get_sandbox(on)
    assert isinstance(built, DockerSandbox)
    assert "bridge" in built._argv("n", "true", tmp_path, [])


def test_it_declares_that_commands_take_it_now_and_an_open_chat_later() -> None:
    """Not "next conversation": that described only the chat's tools, which keep their sandbox.

    A `!` command, a workflow or cron shell step and the verifier build their sandbox on every use,
    so a save of `bridge` reaches them at once — the side that widens access was labelled later
    than it is.
    """
    assert APPLIES_WHEN["CHIMERA_SANDBOX_NETWORK"] == COMMANDS_NOW == "commands_now"


def test_a_save_reaches_the_sandbox_built_per_use_at_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """What the workflow shell step and the verifier call — `get_sandbox()` — sees the save."""
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("CHIMERA_SANDBOX", "docker")
    monkeypatch.setenv("CHIMERA_SANDBOX_NETWORK", "none")
    get_settings.cache_clear()
    before = get_sandbox()

    patch_config({"CHIMERA_SANDBOX_NETWORK": "bridge"}, env_path=tmp_path / ".env")
    after = get_sandbox()
    get_settings.cache_clear()

    assert isinstance(before, DockerSandbox) and before.network is False
    assert isinstance(after, DockerSandbox) and after.network is True


# --- what a command can reach HERE, which is not the setting --------------------------------------


def _sandbox_state(tmp_path: Path, **env: str) -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.interface import ChatSession
    from tests.test_api import _FakeAgent  # noqa: PLC0415

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), **env)  # type: ignore[arg-type]
    client = TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings))
    return dict(client.get("/api/governance/sandbox").json())


def _no_os_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.sandbox import os_sandbox as mod

    monkeypatch.setattr(mod, "os_sandbox_available", lambda: False)


def test_the_host_is_reported_as_the_host_whatever_the_setting_says(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Windows under `auto`: no OS sandbox, so commands run here with this machine's network."""
    _no_os_sandbox(monkeypatch)

    body = _sandbox_state(tmp_path, CHIMERA_SANDBOX="auto", CHIMERA_SANDBOX_NETWORK="none")

    assert body["isolated"] is False
    assert body["network"] == "host", "a 'none' setting was reported as a closed network on the host"


def test_a_container_that_did_not_answer_is_the_host_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _no_os_sandbox(monkeypatch)
    monkeypatch.setattr(DockerSandbox, "available", lambda self: False)

    body = _sandbox_state(tmp_path, CHIMERA_SANDBOX="docker", CHIMERA_SANDBOX_NETWORK="none")

    assert body["backend"] == "host"
    assert body["network"] == "host"


@pytest.mark.parametrize("network", ["none", "bridge"])
def test_a_container_that_answered_has_the_network_it_was_given(
    network: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(DockerSandbox, "available", lambda self: True)

    body = _sandbox_state(tmp_path, CHIMERA_SANDBOX="docker", CHIMERA_SANDBOX_NETWORK=network)

    assert body["backend"] == "docker"
    assert body["network"] == network


def test_a_kernel_sandbox_has_no_network_to_give(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """bubblewrap unshares it, Seatbelt denies it — and the docker setting does not reach either."""
    from chimera.sandbox import os_sandbox as mod

    monkeypatch.setattr(mod, "os_sandbox_available", lambda: True)

    body = _sandbox_state(tmp_path, CHIMERA_SANDBOX="auto", CHIMERA_SANDBOX_NETWORK="bridge")

    assert body["isolated"] is True
    assert body["network"] == "none"


# --- which Python execute_code runs on this machine ------------------------------------------------


def test_from_source_it_is_this_interpreter() -> None:
    assert host_python() == (sys.executable, "interpreter")
    report = host_python_report()
    assert report["path"] == sys.executable
    assert report["source"] == "interpreter"
    assert report["frozen"] is False


def test_a_frozen_build_reports_the_python_it_found_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(code_mod.shutil, "which", lambda name: f"/found/{name}")

    report = host_python_report()

    assert report["source"] == "path"
    assert report["frozen"] is True
    assert report["path"] == f"/found/{code_mod._PATH_PYTHONS[0]}"


def test_a_frozen_build_with_no_python_says_so_and_what_it_looked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(code_mod.shutil, "which", lambda name: None)

    report = host_python_report()

    assert report == {
        "path": "",
        "source": "missing",
        "frozen": True,
        "looked_for": list(code_mod._PATH_PYTHONS),
    }


def test_doctor_names_the_same_python_the_tool_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One function for the screen and the tool, so they cannot name two different interpreters."""
    from chimera.sandbox import LocalSandbox

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(code_mod.shutil, "which", lambda name: f"/found/{name}")

    named = doctor(Settings(CHIMERA_HOME=str(tmp_path)))["code_python"]["path"]  # type: ignore[arg-type]
    command = python_command(LocalSandbox(), "x.py")

    assert named
    assert command is not None and named in command


def test_the_doctor_endpoint_carries_it(tmp_path: Path) -> None:
    from tests.test_api import _client  # noqa: PLC0415

    body = _client(tmp_path).get("/api/doctor").json()

    assert body["code_python"]["source"] == "interpreter"
    assert body["code_python"]["path"] == sys.executable


# --- a Windows App Execution Alias is not a Python until it runs one -------------------------------


def _alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, real: Path | None) -> Path:
    """A frozen build whose PATH holds a WindowsApps alias first and, optionally, a real Python later."""
    apps = tmp_path / "Microsoft" / "WindowsApps"
    apps.mkdir(parents=True)
    alias = apps / "python.exe"
    alias.write_bytes(b"")  # the aliases are zero-byte links
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(code_mod, "_alias_verdicts", {})
    dirs = [str(apps)] + ([str(real.parent)] if real is not None else [])
    monkeypatch.setenv("PATH", os.pathsep.join(dirs))

    def which(name: str, path: str | None = None) -> str | None:
        searched = (path if path is not None else os.environ["PATH"]).split(os.pathsep)
        if name == "python" and str(apps) in searched:
            return str(alias)
        if name == "python" and real is not None and str(real.parent) in searched:
            return str(real)
        return None

    monkeypatch.setattr(code_mod.shutil, "which", which)
    return alias


def _answers(monkeypatch: pytest.MonkeyPatch, code: int) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(argv: list[str], **_kw: Any) -> Any:
        import subprocess  # noqa: PLC0415

        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, code, b"", b"Python was not found")

    monkeypatch.setattr(code_mod.subprocess, "run", run)
    return calls


def test_the_app_installer_placeholder_is_not_reported_as_a_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean Windows: `which("python")` finds the 0-byte alias, which exits 9009 without running.

    The row exists for exactly this machine; naming the placeholder there said Python was present
    while every `execute_code` failed to start.
    """
    _alias(tmp_path, monkeypatch, real=None)
    _answers(monkeypatch, 9009)

    assert host_python() == (None, "missing")
    assert host_python_report()["source"] == "missing"


def test_a_placeholder_first_on_path_does_not_hide_a_real_python_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "Python312" / "python.exe"
    real.parent.mkdir()
    real.write_bytes(b"x")
    _alias(tmp_path, monkeypatch, real=real)
    _answers(monkeypatch, 9009)

    assert host_python() == (str(real), "path")


def test_an_alias_that_runs_python_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Store's Python and the Python install manager live behind the same kind of alias."""
    alias = _alias(tmp_path, monkeypatch, real=None)
    _answers(monkeypatch, 0)

    assert host_python() == (str(alias), "path")


def test_the_alias_is_tried_once_not_on_every_doctor_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alias = _alias(tmp_path, monkeypatch, real=None)
    calls = _answers(monkeypatch, 9009)

    for _ in range(3):
        host_python_report()

    assert calls == [[str(alias), "-c", "import sys"]]


# --- the verifier's exception is reported, so the row can say it -----------------------------------


@pytest.mark.parametrize("on", [False, True])
def test_the_config_reports_the_verifiers_own_network_exception(tmp_path: Path, on: bool) -> None:
    """`core/verify.py` opens a container's network for the verify command, and under a kernel
    sandbox runs one the user typed on the host. A row that says "no network" must be able to know."""
    settings = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_VERIFY_NETWORK=on)  # type: ignore[arg-type]

    assert read_config(settings)["sandbox"]["verify_network"] is on


def test_the_config_endpoint_carries_it(tmp_path: Path) -> None:
    from tests.test_api import _client  # noqa: PLC0415

    body = _client(tmp_path).get("/api/config").json()

    assert body["sandbox"]["verify_network"] is False
