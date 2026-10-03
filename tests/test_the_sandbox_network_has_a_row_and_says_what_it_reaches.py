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

from chimera.api.config_api import APPLIES_WHEN, doctor, is_editable, patch_config, read_config
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
    assert f"CHIMERA_SANDBOX_NETWORK={value}" in env.read_text(encoding="utf-8")
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


def test_it_declares_that_it_waits_for_the_next_conversation() -> None:
    """Read when `default_registry` builds the shell and code tools; an open chat keeps its tools."""
    assert APPLIES_WHEN["CHIMERA_SANDBOX_NETWORK"] == "next_conversation"


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
