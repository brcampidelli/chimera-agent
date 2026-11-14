"""Two process-wide leaks that made one CI shard red and left every single file green.

Each pair below is ORDER-dependent on purpose: the first test leaves the thing behind, the second
asks whether it is still there. Pytest runs tests of a module in file order, so the pair is the
smallest reproduction of what a shard did across files.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from chimera.core import listeners
from chimera.memory.extract import MemoryExtractor
from chimera.memory.manager import MemoryManager
from chimera.memory.store import MemoryStore

PORT = 48151


def test_a_test_claims_a_port_and_ends() -> None:
    listeners.claim(PORT)
    assert PORT in listeners.held()


def test_the_next_test_does_not_inherit_that_port() -> None:
    # `_port()` in a later test can be handed this number by the OS; if it is still "held" the
    # element picker answers 403 where the test expects 409.
    assert PORT not in listeners.held()


class _Slow:
    """A backend that answers after the test body has returned, as a real gateway would."""

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        time.sleep(0.3)
        raise RuntimeError("done")


def test_an_extraction_with_no_backend_never_reaches_a_provider(tmp_path: Path) -> None:
    extractor = MemoryExtractor(MemoryManager(MemoryStore(tmp_path / "m.json")))
    extractor.after_turn("I work at Acme and prefer tabs", "noted")
    for thread in threading.enumerate():
        if thread.name == "memory-extract":
            thread.join(timeout=10)
    assert extractor.last is not None and extractor.last.error is not None
    assert "no test reaches a real model provider" in extractor.last.error


def test_an_extraction_thread_is_finished_before_its_test_is_over(tmp_path: Path) -> None:
    started: list[threading.Thread] = []
    extractor = MemoryExtractor(MemoryManager(MemoryStore(tmp_path / "m.json")), _Slow())
    extractor.after_turn("I work at Acme and prefer tabs", "noted")
    started.extend(t for t in threading.enumerate() if t.name == "memory-extract")
    assert started, "the extraction thread was not started"
    _LEFT.extend(started)


_LEFT: list[threading.Thread] = []


def test_the_thread_of_the_previous_test_was_joined_by_the_fixture() -> None:
    assert _LEFT and not any(t.is_alive() for t in _LEFT)
