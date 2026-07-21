"""The explicit "remember this" capture — English + Portuguese, and the false positives it avoids."""

from __future__ import annotations

import pytest

from chimera.memory.capture import parse_remember_request


@pytest.mark.parametrize(
    "message,expected",
    [
        ("remember that I'm allergic to peanuts", "I'm allergic to peanuts"),
        ("Remember: my flight is on the 12th", "my flight is on the 12th"),
        ("please remember I take my coffee black.", "I take my coffee black"),
        ("don't forget that the wifi password is hunter2", "the wifi password is hunter2"),
        ("note that my manager is Dana", "my manager is Dana"),
        # Portuguese
        ("lembre que meu voo é dia 12", "meu voo é dia 12"),
        ("lembre-se de que sou alérgico a amendoim", "sou alérgico a amendoim"),
        ("guarde que minha reunião é às 15h", "minha reunião é às 15h"),
        ("não esqueça de comprar leite", "comprar leite"),
        ("Anote: o código do portão é 4821", "o código do portão é 4821"),
    ],
)
def test_captures_explicit_requests(message: str, expected: str) -> None:
    assert parse_remember_request(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "I can't remember where I put my keys",  # incidental "remember", not a command
        "do you remember our last conversation?",  # a question, not an instruction
        "não lembro o nome dele",  # PT incidental
        "what's the weather today?",  # unrelated
        "remember",  # trigger with no fact
        "",  # empty
    ],
)
def test_ignores_non_requests(message: str) -> None:
    assert parse_remember_request(message) is None
