"""The dev-server manager: start, stop, ports, the scrubbed environment and the tree kill.

The "server" is a real, tiny Python HTTP server, so the process, the port probe and the kill are the real
ones. What each test would catch if the rule broke: a server that never reports running, a stop that
leaves the port held or a grandchild alive, `autoPort: false` taking another port, a provider key reaching
the child, and a turn being offered a port it did not ask for.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

import psutil
import pytest

from chimera.core.dev_servers import (
    DevServerError,
    DevServerManager,
    DevServerTurn,
    choose_port,
    state_payload,
)
from chimera.core.launch_config import ServerConfig

SERVER = (
    "import os, subprocess, sys, http.server;"
    "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']);"
    "open(sys.argv[1], 'w').write(str(kid.pid));"
    "print('secret=' + str(os.environ.get('MY_API_TOKEN')), flush=True);"
    "print('port=' + os.environ['PORT'], flush=True);"
    "http.server.HTTPServer(('127.0.0.1', int(os.environ['PORT'])),"
    " http.server.SimpleHTTPRequestHandler).serve_forever()"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _config(tmp_path: Path, **extra: object) -> ServerConfig:
    return ServerConfig.model_validate({
        "name": "web", "runtimeExecutable": sys.executable, "runtimeArgs": ["-c", SERVER, str(tmp_path / "kid.pid")],
        "port": _free_port(), **extra,
    })


def _wait(predicate: object, seconds: float = 20.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def mgr():
    manager = DevServerManager()
    yield manager
    manager.close_all()


def test_a_server_starts_reports_running_and_stops(mgr: DevServerManager, tmp_path: Path) -> None:
    config = _config(tmp_path)
    server = mgr.start(tmp_path, config, started_by="owner")
    assert _wait(lambda: server.state() == "running"), mgr.tail(tmp_path, "web")
    assert server.port == config.port
    assert state_payload(server, config)["url"] == f"http://localhost:{config.port}"
    assert any(line.startswith("port=") for line in mgr.tail(tmp_path, "web", 20))

    assert mgr.stop(tmp_path, "web") is True
    assert server.state() in ("exited", "failed")
    assert not _wait(lambda: not _port_open(config.port), 0.1) or True  # port released below
    assert _wait(lambda: not _port_open(config.port), 5)
    assert mgr.stop(tmp_path, "web") is False  # nothing left to stop


def _port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def test_starting_twice_returns_the_same_running_server(mgr: DevServerManager, tmp_path: Path) -> None:
    config = _config(tmp_path)
    first = mgr.start(tmp_path, config, started_by="owner")
    assert mgr.start(tmp_path, config, started_by="turn") is first
    assert first.started_by == "owner"


def test_stopping_kills_the_whole_tree(mgr: DevServerManager, tmp_path: Path) -> None:
    server = mgr.start(tmp_path, _config(tmp_path), started_by="owner")
    assert _wait(lambda: server.state() == "running")
    assert _wait(lambda: (tmp_path / "kid.pid").exists())
    kid = int((tmp_path / "kid.pid").read_text())
    assert psutil.pid_exists(kid)
    mgr.stop(tmp_path, "web")
    assert _wait(lambda: not psutil.pid_exists(kid), 10), "the grandchild outlived the stop"


def test_closing_the_app_kills_every_server(tmp_path: Path) -> None:
    mgr = DevServerManager()
    server = mgr.start(tmp_path, _config(tmp_path), started_by="owner")
    assert _wait(lambda: server.state() == "running")
    assert server.proc is not None
    pid = server.proc.pid
    mgr.close_all()
    assert _wait(lambda: not psutil.pid_exists(pid), 10)


def test_closing_the_app_kills_the_tree_even_when_the_direct_child_is_already_dead(tmp_path: Path) -> None:
    mgr = DevServerManager()
    server = mgr.start(tmp_path, _config(tmp_path), started_by="owner")
    assert _wait(lambda: server.state() == "running")
    assert _wait(lambda: (tmp_path / "kid.pid").exists())
    kid = int((tmp_path / "kid.pid").read_text())
    assert server.proc is not None
    leader = psutil.Process(server.proc.pid)
    if os.name == "nt":
        # No group to address once the leader is gone: only the job object reaches the survivors.
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(1, False, leader.pid)  # PROCESS_TERMINATE
        ctypes.windll.kernel32.TerminateProcess(handle, 1)
        ctypes.windll.kernel32.CloseHandle(handle)
    else:
        leader.kill()
    assert _wait(lambda: server.proc is not None and server.proc.poll() is not None, 10)
    assert psutil.pid_exists(kid), "the grandchild should outlive its dead parent for this test to mean anything"
    mgr.close_all()
    assert _wait(lambda: not psutil.pid_exists(kid), 10), "close_all left the grandchild alive"


@pytest.mark.skipif(os.name != "nt", reason="the job object is Windows-only")
def test_on_windows_no_job_object_means_no_server(
    mgr: DevServerManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.proc import winjob

    monkeypatch.setattr(winjob, "contain", lambda pid: None)
    config = _config(tmp_path)
    with pytest.raises(DevServerError, match="Job Object"):
        mgr.start(tmp_path, config, started_by="owner")
    assert not _wait(lambda: _port_open(config.port), 1.5)  # and nothing is left listening


def test_the_child_does_not_inherit_provider_secrets(
    mgr: DevServerManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_API_TOKEN", "hunter2-hunter2")
    server = mgr.start(tmp_path, _config(tmp_path), started_by="owner")
    assert _wait(lambda: server.state() == "running")
    log = "\n".join(mgr.tail(tmp_path, "web", 20))
    assert "secret=None" in log and "hunter2" not in log


def test_auto_port_false_refuses_a_busy_port_and_the_default_moves_on(tmp_path: Path) -> None:
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(64)
        busy = int(holder.getsockname()[1])
        pinned = ServerConfig.model_validate(
            {"name": "a", "runtimeExecutable": "x", "port": busy, "autoPort": False}
        )
        with pytest.raises(DevServerError, match="autoPort: false"):
            choose_port(pinned)
        free = ServerConfig.model_validate({"name": "b", "runtimeExecutable": "x", "port": busy})
        assert choose_port(free) != busy


def test_a_command_that_does_not_exist_is_a_sentence_not_a_crash(mgr: DevServerManager, tmp_path: Path) -> None:
    config = ServerConfig.model_validate(
        {"name": "x", "runtimeExecutable": "definitely-not-a-program-xyz", "port": _free_port()}
    )
    with pytest.raises(DevServerError, match="could not be started"):
        mgr.start(tmp_path, config, started_by="owner")


def test_a_server_that_exits_is_failed_with_its_output(mgr: DevServerManager, tmp_path: Path) -> None:
    config = ServerConfig.model_validate({
        "name": "boom", "runtimeExecutable": sys.executable,
        "runtimeArgs": ["-c", "print('bad config'); raise SystemExit(3)"], "port": _free_port(),
    })
    server = mgr.start(tmp_path, config, started_by="owner")
    assert _wait(lambda: server.state() == "failed")
    assert server.exit_code() == 3
    assert _wait(lambda: "bad config" in "\n".join(mgr.tail(tmp_path, "boom")))


def test_a_cwd_that_is_not_a_folder_is_refused(mgr: DevServerManager, tmp_path: Path) -> None:
    config = _config(tmp_path, cwd="missing")
    with pytest.raises(DevServerError, match="working directory"):
        mgr.start(tmp_path, config, started_by="owner")


def test_an_attached_server_is_never_started_or_killed(mgr: DevServerManager, tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(64)
        port = int(listener.getsockname()[1])
        config = ServerConfig.model_validate({"name": "ext", "url": f"http://localhost:{port}", "port": port})
        server = mgr.start(tmp_path, config, started_by="owner")
        assert server.proc is None and server.state() == "running"
        assert mgr.stop(tmp_path, "ext") is True
        assert _port_open(port)  # ours to forget, not to close
    with pytest.raises(DevServerError, match="nothing is answering"):
        mgr.start(tmp_path, config, started_by="owner")


def test_a_turn_is_offered_only_the_ports_it_asked_for(mgr: DevServerManager, tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(64)
        port = int(listener.getsockname()[1])
        config = ServerConfig.model_validate({"name": "ext", "url": f"http://localhost:{port}", "port": port})
        mgr.start(tmp_path, config, started_by="owner")
        turn = DevServerTurn(tmp_path, mgr)
        assert turn.ports() == frozenset()  # running, but not this turn's
        turn.adopt("ext")
        # Asked for by name and answering, but nobody's process tree here listens on it: not a turn port.
        assert turn.ports() == frozenset()
        assert DevServerTurn(tmp_path, mgr).ports() == frozenset()


def test_a_spawned_servers_port_is_the_turns_only_through_its_own_tree(mgr: DevServerManager, tmp_path: Path) -> None:
    config = _config(tmp_path)
    server = mgr.start(tmp_path, config, started_by="owner")
    assert _wait(lambda: server.state() == "running")
    turn = DevServerTurn(tmp_path, mgr)
    assert turn.ports() == frozenset()  # not asked for
    turn.adopt("web")
    assert _wait(lambda: turn.ports() == frozenset({config.port}), 10)


def test_attaching_to_a_port_a_chimera_server_holds_is_refused(mgr: DevServerManager, tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(64)
        port = int(listener.getsockname()[1])
        config = ServerConfig.model_validate({"name": "ext", "url": f"http://localhost:{port}", "port": port})
        with pytest.raises(DevServerError, match="Chimera itself"):
            mgr.start(tmp_path, config, started_by="owner", forbidden_ports={port})
        assert mgr.get(tmp_path, "ext") is None


@pytest.mark.skipif(os.name != "nt", reason="the job object is Windows-only")
def test_on_windows_the_server_sits_in_a_job_object(mgr: DevServerManager, tmp_path: Path) -> None:
    server = mgr.start(tmp_path, _config(tmp_path), started_by="owner")
    assert server.job is not None
