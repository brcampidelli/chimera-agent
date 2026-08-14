"""The shipped default that put an unauthenticated agent on the public internet.

Three things, each defensible alone: the compose published `8765:8765` on every interface,
`.env.example` left the token empty, and auth is opt-in — no token configured means no check. Follow
the README's one-command deployment and you get an agent with tools answering anyone who finds the
port. Nobody chose that; it is what the three defaults compose into.

The check is on the BIND rather than on the request, and that is the design. A gateway that starts
and then 401s has already announced itself, and still depends on every future transport branch
remembering to ask — which is exactly how the A2A streaming path once served an unauthenticated
agent by short-circuiting ahead of the auth call.
"""

from __future__ import annotations

import pytest

from chimera.server.bind import InsecureBindError, check_bind, is_loopback

# --- what counts as reachable -------------------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.53", ""])
def test_loopback_is_recognised_by_name_and_by_number(host: str) -> None:
    # `localhost` is the spelling almost everyone types, and on a normal machine it is exactly as
    # private as the two addresses beside it. Missing it would make the guard fire on the safe case.
    assert is_loopback(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "2.24.95.82", "chimera.example"])
def test_anything_reachable_is_treated_as_reachable(host: str) -> None:
    assert is_loopback(host) is False


def test_an_unclassifiable_hostname_is_assumed_reachable() -> None:
    """The conservative direction. Being wrong here costs one flag on a command line; being wrong
    the other way costs an unauthenticated agent on a public interface."""
    assert is_loopback("some-host-we-cannot-resolve") is False


# --- the refusal --------------------------------------------------------------------------------


def test_it_refuses_a_public_bind_with_no_token() -> None:
    with pytest.raises(InsecureBindError) as caught:
        check_bind("0.0.0.0", token="")

    message = str(caught.value)
    assert "CHIMERA_SERVER_TOKEN" in message
    assert "--allow-insecure-bind" in message, "a refusal with no way forward is a wall, not a guard"


def test_loopback_needs_no_token() -> None:
    # The default, and it has to stay frictionless: a guard that fires on `chimera serve` with no
    # arguments would be worked around by everyone within a week.
    check_bind("127.0.0.1", token="")


def test_a_token_is_what_makes_a_public_bind_legitimate() -> None:
    check_bind("0.0.0.0", token="a-long-random-string")


def test_whitespace_is_not_a_token() -> None:
    # `CHIMERA_SERVER_TOKEN=" "` in an .env is a typo, not a decision.
    with pytest.raises(InsecureBindError):
        check_bind("0.0.0.0", token="   ")


def test_the_escape_exists_because_a_trusted_proxy_is_a_real_deployment() -> None:
    """Without it this correction is a regression for everyone already serving behind an
    authenticating proxy — and a guard people must work around is one they learn to disable
    everywhere."""
    check_bind("0.0.0.0", token="", allow_insecure=True)
