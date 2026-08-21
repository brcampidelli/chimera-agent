"""The two Settings toggles that fired nothing from the app.

"Remember from chat" was real, correctly gated, and reachable only from ``/api/chat/stream`` — a
route with no callers in the desktop, whose every conversation goes through ``/api/code/turn``.
"Tidy memory" was read only inside the CLI REPL's own loop, so it did nothing unless the same person
also ran ``chimera chat`` against the same home. The empty Memory screen meanwhile advertised the
first one as the way to fill memory.

The write is narrow on purpose, and the tests below are mostly about the edges of that narrowness:
the user's own typed sentence is written, and nothing derived from the agent, the tools or a file
ever is.
"""

from __future__ import annotations

from typing import Any

from chimera.api.code_api import _remember_and_tidy


class _Settings:
    def __init__(
        self, *, remember: bool = True, tidy: bool = False, budget: int = 100
    ) -> None:
        self.remember_from_chat = remember
        self.auto_consolidate = tidy
        self.memory_budget = budget


class _Memory:
    """Duck-typed like MemoryManager for the two methods this path touches."""

    def __init__(self, *, removes: int = 0, writable: bool = True) -> None:
        self.written: list[tuple[str, str]] = []
        self.consolidations = 0
        self._removes = removes
        if not writable:
            # An attribute, not a method: `_remember_and_tidy` duck-types on callable(), which is
            # what a search-only backend actually looks like.
            self.remember = None  # type: ignore[assignment]

    def remember(self, fact: str, *, source: str = "") -> None:
        self.written.append((fact, source))

    def autoconsolidate(self, _summarizer: Any, *, max_items: int) -> int:
        self.consolidations += 1
        return self._removes


def test_an_explicit_remember_from_the_users_own_message_is_saved() -> None:
    memory = _Memory()

    saved, tidied = _remember_and_tidy("lembre que meu voo é dia 12", memory, _Settings())

    assert saved == "meu voo é dia 12"
    assert memory.written == [("meu voo é dia 12", "chat")]
    assert tidied == 0, "tidying is off in this configuration"


def test_an_ordinary_message_writes_nothing() -> None:
    """The trigger is anchored to the start of the message, and this is the reason why.

    Automatic extraction is what pollutes memory: a fact has no project scope, so anything written
    here is recalled into every other conversation forever.
    """
    memory = _Memory()

    saved, _ = _remember_and_tidy("I can't remember where I put the config file", memory, _Settings())

    assert saved is None
    assert memory.written == []


def test_the_toggle_being_off_means_off() -> None:
    memory = _Memory()

    saved, _ = _remember_and_tidy("remember that I use tabs", memory, _Settings(remember=False))

    assert saved is None and memory.written == []


def test_tidying_runs_after_a_write_and_only_after_one() -> None:
    """Memory only grows when something is written, so the moment after a write is the moment to
    check — and `autoconsolidate` returns 0 without calling a model while memory is under budget."""
    wrote = _Memory(removes=3)
    saved, tidied = _remember_and_tidy(
        "remember that I use tabs", wrote, _Settings(tidy=True)
    )
    assert saved == "I use tabs" and tidied == 3 and wrote.consolidations == 1

    quiet = _Memory(removes=3)
    _remember_and_tidy("just a normal question", quiet, _Settings(tidy=True))
    assert quiet.consolidations == 0, "nothing was written, so nothing can have outgrown anything"


def test_tidying_off_leaves_the_write_alone() -> None:
    memory = _Memory(removes=3)

    saved, tidied = _remember_and_tidy("remember that I use tabs", memory, _Settings(tidy=False))

    assert saved == "I use tabs" and tidied == 0 and memory.consolidations == 0


def test_a_memory_that_cannot_write_is_skipped_not_crashed() -> None:
    """A search-only backend has no `remember`. Duck-typed, as the CLI path already was."""
    assert _remember_and_tidy("remember that x", _Memory(writable=False), _Settings()) == (None, 0)
    assert _remember_and_tidy("remember that x", None, _Settings()) == (None, 0)


def test_a_failing_write_never_takes_the_turn_down() -> None:
    """The answer is the product. A memory backend with a full disk must not lose it."""

    class _Broken(_Memory):
        def remember(self, fact: str, *, source: str = "") -> None:
            raise OSError("disk full")

    assert _remember_and_tidy("remember that x", _Broken(), _Settings()) == (None, 0)


def test_a_failing_consolidation_still_reports_the_fact_that_was_saved() -> None:
    """Consolidation calls a model, so it can fail for reasons the write cannot — no key, no
    network, a rate limit. Losing the confirmation of a write that DID happen would tell the user
    their fact was not saved when it was."""

    class _BadTidy(_Memory):
        def autoconsolidate(self, _summarizer: Any, *, max_items: int) -> int:
            raise RuntimeError("no api key")

    saved, tidied = _remember_and_tidy(
        "remember that I use tabs", _BadTidy(), _Settings(tidy=True)
    )

    assert saved == "I use tabs" and tidied == 0
