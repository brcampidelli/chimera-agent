"""Asking the local Ollama what it has, instead of asking the user to remember.

Every model field on the Settings screen that names an Ollama tag was a free-text box filled from
memory, and a wrong tag does not fail at save time — it fails on the first call, mid-run, as a 404
from a server the user believed was ready.

The assertion this file exists for is the one about EMPTY. "Ollama is not running" and "Ollama is
running with nothing pulled" have opposite remedies, and both arrive at a picker as zero options. A
module that returned only a list would make the screen say *you have no models* about a machine it
did not manage to ask, which is a claim we cannot support and the user cannot debug.

Nothing here starts a server. `httpx.get` is replaced, because what is under test is how each answer
is READ — a live Ollama would test that the machine running CI happens to have one.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from chimera.providers.ollama import installed_models


class _Response:
    """Just enough of `httpx.Response` for the reader: a status, and a body that may not be JSON."""

    def __init__(self, status_code: int, payload: Any = None, *, valid_json: bool = True) -> None:
        self.status_code = status_code
        self._payload = payload
        self._valid_json = valid_json

    def json(self) -> Any:
        if not self._valid_json:
            raise ValueError("not json")
        return self._payload


def _answer(monkeypatch: pytest.MonkeyPatch, response: _Response | Exception) -> None:
    def fake_get(url: str, **kwargs: Any) -> _Response:
        assert url.endswith("/api/tags"), f"asked {url}, which is not Ollama's tag endpoint"
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(httpx, "get", fake_get)


def test_the_tags_come_back_sorted_and_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer(
        monkeypatch,
        _Response(
            200,
            {
                "models": [
                    {"name": "qwen2.5-coder:1.5b-base"},
                    {"name": "llama3:latest"},
                    {"name": "llama3:latest"},  # Ollama lists a tag once; a duplicate must not double
                ]
            },
        ),
    )

    found = installed_models("http://localhost:11434")

    assert found.reachable is True
    assert found.models == ("llama3:latest", "qwen2.5-coder:1.5b-base")
    assert found.reason == ""


def test_an_ollama_with_nothing_pulled_is_reachable_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole reason `reachable` is a field rather than `len(models) > 0`.

    A server that answered with an empty list has told us something true about the machine — there is
    an Ollama here and it holds nothing. Reporting that as unreachable, or as an ordinary empty list,
    sends the user to start a daemon that is already running.
    """
    _answer(monkeypatch, _Response(200, {"models": []}))

    found = installed_models("http://localhost:11434")

    assert found.reachable is True
    assert found.models == ()
    assert found.reason == ""


def test_a_server_that_is_not_there_is_not_a_machine_with_no_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer(monkeypatch, httpx.ConnectError("connection refused"))

    found = installed_models("http://localhost:11434")

    assert found.reachable is False
    assert found.reason == "unreachable"
    assert found.base_url == "http://localhost:11434"  # named, so the client can say WHICH url


def test_an_unset_url_is_its_own_answer() -> None:
    """Distinct from "unreachable": nothing was asked, so nothing failed.

    They read the same to a picker and differently to a person — one wants a URL typed, the other
    wants a daemon started.
    """
    found = installed_models("")

    assert found.reachable is False
    assert found.reason == "no_url"


def test_a_refusal_is_not_confused_with_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer(monkeypatch, _Response(500))

    assert installed_models("http://localhost:11434").reason == "http_error"


@pytest.mark.parametrize(
    "body",
    [
        _Response(200, valid_json=False),  # an HTML login page from a proxy in front of the port
        _Response(200, {"nope": []}),  # JSON, but not Ollama's shape
        _Response(200, {"models": "llama3"}),  # the right key holding the wrong type
    ],
)
def test_something_else_on_that_port_is_reported_as_something_else(
    monkeypatch: pytest.MonkeyPatch, body: _Response
) -> None:
    """A 200 is not evidence of an Ollama. Ports get reused, and proxies answer for hosts that are
    down — reading either as "an Ollama with no models" would blame the user's model library for a
    networking problem."""
    _answer(monkeypatch, body)

    assert installed_models("http://localhost:11434").reason == "not_ollama"


def test_a_trailing_slash_does_not_become_a_double_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    """`http://host:11434/` is what a browser's address bar hands you, so it is what people paste."""
    _answer(monkeypatch, _Response(200, {"models": [{"name": "llama3:latest"}]}))

    found = installed_models("http://localhost:11434/")

    assert found.models == ("llama3:latest",)  # the assert inside `fake_get` did the real checking
    assert found.base_url == "http://localhost:11434"


def test_a_nameless_entry_is_dropped_rather_than_rendered_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blank option in a picker is a thing the user can select and then cannot explain."""
    _answer(
        monkeypatch,
        _Response(200, {"models": [{"name": ""}, {"name": "   "}, {}, "llama3", {"name": "ok:1"}]}),
    )

    assert installed_models("http://localhost:11434").models == ("ok:1",)
