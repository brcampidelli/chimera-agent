"""Learning that nothing is listening should cost a fraction of a second, not two.

The Settings screen asks the local Ollama what it has pulled every time a model picker mounts, and
that question is deliberately never cached — a tag pulled in a terminal thirty seconds ago is the
one the user came here to select. So the cost of asking is paid over and over, and on the machines
where the answer is "nothing is there" it was the most expensive question the app asked.

Why it was expensive is not what anyone would guess, so it is worth writing down. Measured on
Windows: a loopback port with something listening accepts in at most 16 ms over 30 attempts. The
same port with nothing behind it takes **2.04 s** to come back *refused* — not to time out, to be
actively refused. And `localhost` resolves to `::1` as well as `127.0.0.1`, so the whole thing is
paid twice: 4.2 s of waiting, capped by an outer deadline at 2.0 s, to learn something the first
millisecond had already settled.

None of that is reducible per attempt. What IS separable is opening the connection from waiting for
a reply, and this file is about that split holding: short to connect when the address is on this
machine, unchanged when it is not.

Nothing here opens a socket. `httpx.get` is replaced and the budget it receives is read off the
call, because what is under test is which budget is chosen — a real probe would test whether the
machine running CI happens to be running Ollama.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from chimera.providers.ollama import DEFAULT_TIMEOUT_S, LOCAL_CONNECT_TIMEOUT_S, installed_models


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Answer every probe with an empty, valid tag list and keep the timeout it was given."""
    seen: dict[str, Any] = {}

    class _Ok:
        status_code = 200

        def json(self) -> Any:
            return {"models": []}

    def fake_get(url: str, **kwargs: Any) -> Any:
        seen["url"] = url
        seen["timeout"] = kwargs.get("timeout")
        return _Ok()

    monkeypatch.setattr(httpx, "get", fake_get)
    return seen


def _connect_of(timeout: Any) -> float:
    assert isinstance(timeout, httpx.Timeout), (
        f"the probe was given {timeout!r}, which budgets connecting and reading together — "
        "the whole point is that they are budgeted apart"
    )
    connect = timeout.connect
    assert connect is not None
    return float(connect)


def test_a_port_on_this_machine_is_given_a_short_moment_to_accept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _capture(monkeypatch)
    installed_models("http://localhost:11434")
    connect = _connect_of(seen["timeout"])
    assert connect == LOCAL_CONNECT_TIMEOUT_S
    # The number that matters is not the constant, it is the ratio: this is what turns 2.04s of
    # waiting into a quarter of one, twice over.
    assert connect < DEFAULT_TIMEOUT_S / 4


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://127.1.2.3:11434",  # the whole 127/8 block is this machine, not just .0.0.1
        "http://[::1]:11434",
        "http://LOCALHOST:11434",  # a host is case-insensitive and users type it either way
    ],
)
def test_every_way_of_naming_this_machine_gets_the_short_budget(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    seen = _capture(monkeypatch)
    installed_models(url)
    assert _connect_of(seen["timeout"]) == LOCAL_CONNECT_TIMEOUT_S


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.40:11434",  # the desktop in the next room
        "http://ollama.lan:11434",  # named on the LAN
        "https://ollama.example.com",  # across a VPN
        "http://10.0.0.7:11434",
    ],
)
def test_a_machine_that_is_not_this_one_keeps_the_full_budget(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    """The saving is worth a fraction of a second. Calling someone's VPN'd Ollama unreachable to
    collect it would be inventing a fact about their machine, which is what this module exists to
    not do."""
    seen = _capture(monkeypatch)
    installed_models(url)
    assert _connect_of(seen["timeout"]) == DEFAULT_TIMEOUT_S


def test_a_caller_asking_for_a_fast_probe_is_not_handed_a_slower_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`timeout_s` is the whole budget. A connect allowance longer than it would mean the deadline
    fires first and the split silently stops applying."""
    seen = _capture(monkeypatch)
    installed_models("http://localhost:11434", timeout_s=0.05)
    assert _connect_of(seen["timeout"]) == 0.05


def test_the_read_budget_is_not_shortened_along_with_the_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A daemon that accepted and is slow to answer is a live server paging itself back in. It has
    always been given the full wait and this change must not take it away."""
    seen = _capture(monkeypatch)
    installed_models("http://localhost:11434")
    timeout = seen["timeout"]
    assert timeout.read == DEFAULT_TIMEOUT_S
    assert timeout.write == DEFAULT_TIMEOUT_S


def test_a_url_too_broken_to_split_still_answers_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This is called to fill in a form. Whatever the user typed, the answer is a reason, not a 500."""
    _capture(monkeypatch)
    found = installed_models("http://[oops:11434")
    assert found.reachable in (True, False)  # it returned at all, which is the assertion


def test_what_the_probe_reports_did_not_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """The split is about waiting, not about answers. A reachable Ollama with nothing pulled still
    reads as reachable-and-empty, which is the distinction the whole module is built around."""
    _capture(monkeypatch)
    found = installed_models("http://localhost:11434")
    assert found.reachable is True
    assert found.models == ()
    assert found.reason == ""
