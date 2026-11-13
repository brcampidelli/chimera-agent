"""Refuse a socket connection that leaves this machine, for every test that did not ask for one.

``pyproject.toml`` says the default run is hermetic (``-m 'not integration'``), but that is a marker
convention: nothing stopped a unit test from reaching a real server. One that did passed on a
developer's laptop, failed on an offline runner, and, worse, passed on the runner too when the
server happened to answer, so the test's verdict depended on a third party's uptime.

The patch is on ``socket.socket.connect`` / ``connect_ex``, which is where ``socket.create_connection``,
``http.client``, ``urllib`` and ``httpx`` all end up on the selector event loop (Linux and macOS, where
CI runs). Windows' proactor loop connects through overlapped I/O and does not pass here, so a leak that
only a Windows developer's machine would make is caught on the Linux runner, not locally.

A swallowed exception is not enough of a signal (``except Exception`` is everywhere in this code base,
and a fallback path can make the test pass anyway), so each refusal is also recorded and the TEST is
failed at teardown. Local addresses are untouched: the test client, the loopback servers and the
Unix-domain sockets the suite starts are the point of the suite.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

#: Marks a test that is allowed to reach the network. ``integration`` is the other way in.
OPT_OUT_MARKERS = ("network", "integration")

_LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}


class NetworkAccessInTest(RuntimeError):
    """Not an ``OSError`` on purpose: a caller's ``except OSError`` fallback must not absorb it."""


def is_local(address: Any) -> bool:
    """True for a Unix-domain address, a loopback address, or the unspecified address."""
    if not isinstance(address, tuple):
        return True  # AF_UNIX: a path, never a network
    host = address[0]
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    if host == "" or host.lower() in _LOCAL_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False  # a hostname that is not localhost: the connect would resolve it elsewhere
    return ip.is_loopback or ip.is_unspecified


@contextmanager
def refusing_outside_connections(violations: list[str]) -> Iterator[None]:
    """Patch ``socket.socket.connect``/``connect_ex``; append each refused address to ``violations``."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guard(real: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(self: socket.socket, address: Any, *args: Any) -> Any:
            # A UDP "connect" only sets the default destination in the kernel; nothing is sent.
            # `chimera/api/sharing.py` uses it to ask the routing table which address is primary.
            if self.type != socket.SOCK_DGRAM and not is_local(address):
                violations.append(repr(address))
                raise NetworkAccessInTest(
                    f"a test tried to connect to {address!r}; mark it `network` (or `integration`) "
                    "if reaching a real server is the point, otherwise fake the transport"
                )
            return real(self, address, *args)

        return wrapper

    socket.socket.connect = guard(real_connect)  # type: ignore[method-assign,assignment]
    socket.socket.connect_ex = guard(real_connect_ex)  # type: ignore[method-assign,assignment]
    try:
        yield
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]


ALLOWLIST_FILE = "network_allowlist.txt"


def load_allowlist(path: Path) -> list[str]:
    """Test ids (or file paths) that may reach the network until their transport is faked."""
    entries: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            entries.append(line)
    return entries


def is_allowlisted(node_id: str, entries: list[str]) -> bool:
    """Exact id, a parametrised variant of it (``id[...]``), or a whole file (``tests/x.py``)."""
    for entry in entries:
        if node_id == entry or node_id.startswith((entry + "[", entry + "::")):
            return True
    return False
