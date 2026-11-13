"""The suite is hermetic by construction, not by marker convention (`tests/network_guard.py`)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests.network_guard import NetworkAccessInTest, is_local, refusing_outside_connections


@pytest.mark.parametrize(
    "address",
    [("127.0.0.1", 80), ("localhost", 80), ("::1", 80, 0, 0), ("0.0.0.0", 80), "/tmp/a.sock", ("", 80)],
)
def test_this_machine_is_local(address: object) -> None:
    assert is_local(address)


@pytest.mark.parametrize("address", [("93.184.216.34", 80), ("example.com", 443), ("2606:4700::1111", 443, 0, 0)])
def test_anywhere_else_is_not(address: object) -> None:
    assert not is_local(address)


def test_a_connection_outside_is_refused_and_recorded() -> None:
    violations: list[str] = []
    with refusing_outside_connections(violations):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(NetworkAccessInTest):
                sock.connect(("93.184.216.34", 80))  # never sent: refused before the kernel is asked
            with pytest.raises(NetworkAccessInTest):
                sock.connect_ex(("example.com", 80))
        finally:
            sock.close()
    assert len(violations) == 2


def test_the_refusal_is_not_an_oserror_a_fallback_could_absorb() -> None:
    assert not issubclass(NetworkAccessInTest, OSError)


def test_a_loopback_connection_still_works() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        client = socket.create_connection(server.getsockname(), timeout=2)
        client.close()
    finally:
        server.close()


def test_the_patch_is_removed_afterwards() -> None:
    real = socket.socket.connect
    with refusing_outside_connections([]):
        assert socket.socket.connect is not real
    assert socket.socket.connect is real


def test_the_suite_wide_fixture_is_on_for_an_ordinary_test() -> None:
    """Not the context manager: the autouse fixture in conftest, which is what protects every test."""
    assert socket.socket.connect.__name__ == "wrapper"


_INNER_CONFTEST = '''\
import pytest
from tests.network_guard import OPT_OUT_MARKERS, refusing_outside_connections


@pytest.fixture(autouse=True)
def guard(request):
    if any(request.node.get_closest_marker(n) for n in OPT_OUT_MARKERS):
        yield
        return
    v = []
    with refusing_outside_connections(v):
        yield
    if v:
        pytest.fail("connected outside", pytrace=False)
'''

_INNER_TEST = '''\
import socket

import pytest


def test_swallowed():
    s = socket.socket()
    try:
        s.connect(("93.184.216.34", 80))
    except Exception:
        pass  # a fallback path that hides the refusal


@pytest.mark.network
def test_allowed_by_marker():
    pass
'''


def test_a_test_that_swallows_the_refusal_still_fails(tmp_path: Path) -> None:
    """A fallback that hides the exception must not hide the connection: the TEST fails at teardown."""
    (tmp_path / "conftest.py").write_text(_INNER_CONFTEST, encoding="utf-8")
    (tmp_path / "test_inner.py").write_text(_INNER_TEST, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers = network: x\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert "2 passed, 1 error" in done.stdout, done.stdout + done.stderr


def test_every_allowlisted_test_still_exists() -> None:
    """The debt list cannot name a test that was renamed or deleted, or it would excuse nothing and linger."""
    from tests.network_guard import ALLOWLIST_FILE, is_allowlisted, load_allowlist

    here = Path(__file__).resolve().parent
    entries = load_allowlist(here / ALLOWLIST_FILE)
    stale: list[str] = []
    for entry in entries:
        file_part, _, name = entry.partition("::")
        source = here.parent / file_part
        if not source.is_file() or (name and f"def {name.split('[', 1)[0]}(" not in source.read_text(encoding="utf-8")):
            stale.append(entry)
    assert stale == []
    assert is_allowlisted("tests/a.py::test_x[1]", ["tests/a.py::test_x"])
    assert not is_allowlisted("tests/a.py::test_xy", ["tests/a.py::test_x"])
