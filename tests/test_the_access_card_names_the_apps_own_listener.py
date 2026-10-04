"""The access card names where the app's own listener is bound, and calls a network bind a door.

Found by an adversarial review of study 29, P5.5. ``chimera desktop --host 0.0.0.0`` is a supported
option (the CLI even maps 0.0.0.0 to loopback for the bridge's URL), and then the guest app mounted
at ``/guest`` on the app's own listener answers the network with any share link. The card looked
only at the separate LAN listener and said "Closed. Share links open only from this computer."

What is pinned: ``GET /api/security/access`` reports ``server`` — the bound host and port, and
``network`` true for anything that is not a loopback address (a host name other than ``localhost``
included: the card would rather call a door open than an open one shut); a process that was not
started by ``chimera desktop`` says it does not know rather than guessing; and ``chimera desktop``
hands the address it bound to the app.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from chimera.api.access_api import is_loopback_host
from tests.test_every_way_into_this_machine_is_on_one_card import _build


@pytest.mark.parametrize(
    ("host", "network"),
    [
        ("127.0.0.1", False),
        ("127.0.0.2", False),
        ("::1", False),
        ("[::1]", False),
        ("localhost", False),
        ("0.0.0.0", True),
        ("::", True),
        ("192.168.1.20", True),
        ("my-machine.local", True),
    ],
)
def test_the_card_reports_the_apps_bind_and_whether_the_network_reaches_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str, network: bool
) -> None:
    assert is_loopback_host(host) is (not network)
    client = _build(tmp_path, monkeypatch)
    app: Any = client.app
    app.state.bound_address = (host, 8765)
    server = client.get("/api/security/access").json()["server"]
    assert server == {"bind": host, "port": 8765, "network": network}


def test_an_app_nobody_bound_says_it_does_not_know_rather_than_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _build(tmp_path, monkeypatch)
    server = client.get("/api/security/access").json()["server"]
    assert server == {"bind": None, "port": None, "network": False}


def test_chimera_desktop_hands_the_app_the_address_it_bound() -> None:
    """Structural, like `test_the_app_does_not_hand_the_api_a_frozen_settings`: the desktop command
    cannot be run in a test, and the one line that feeds the card lives in it."""
    source = (Path(__file__).resolve().parents[1] / "chimera" / "cli" / "main.py").read_text(
        encoding="utf-8"
    )
    found = re.search(
        r"sock, port = _bind_app_socket\(host, port\)\n(?:\s*#[^\n]*\n)*"
        r"\s*api\.state\.bound_address = \(host, port\)\n",
        source,
    )
    assert found is not None, (
        "chimera desktop no longer tells the app where it bound — the access card would call a "
        "listener on 0.0.0.0 closed"
    )
