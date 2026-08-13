"""Inline completion, against a model server we control.

There is no Ollama on the machine these were written on, and that is not the limitation it sounds
like: what has to be proved here is **our** half. A real Ollama would tell me the suggestions are
plausible, which is not a claim this phase is allowed to make; what it would NOT tell me is whether
a superseded request stops occupying the GPU, because from outside both look like "no answer".

So the server is a socket we own, and it reports the one fact that settles it: when the connection
was closed. Ollama stops generating when the client hangs up — that is the mechanism — so proving
we hang up early proves the cancellation end to end, on a machine with no GPU in it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import threading
import time
from typing import Any

import pytest

from chimera.complete.inline import complete_inline, reset_slots


class FakeOllama:
    """A raw HTTP/1.1 server that streams NDJSON forever until the client goes away."""

    def __init__(self, *, chunks: list[str] | None = None, status: int = 200, forever: bool = False):
        self.chunks = chunks if chunks is not None else ["hello", " world"]
        self.status = status
        self.forever = forever
        self.requests: list[dict[str, Any]] = []
        #: How long each connection lasted before the peer hung up, BY ORDER OF ARRIVAL. Keyed and
        #: not appended, because "the first connection" is the one under test and a list ordered by
        #: close time puts the leaked one last — which is exactly the case that must fail loudly.
        self.durations: dict[int, float] = {}
        self._accepted = 0
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.url = f"http://127.0.0.1:{self._sock.getsockname()[1]}"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            index = self._accepted
            self._accepted += 1
            threading.Thread(target=self._handle, args=(conn, index), daemon=True).start()

    def _handle(self, conn: socket.socket, index: int) -> None:
        started = time.monotonic()
        try:
            conn.settimeout(5.0)
            raw = b""
            while b"\r\n\r\n" not in raw:
                part = conn.recv(65536)
                if not part:
                    return
                raw += part
            head, _, rest = raw.partition(b"\r\n\r\n")
            length = 0
            for line in head.decode("latin-1").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1])
            while len(rest) < length:
                part = conn.recv(65536)
                if not part:
                    break
                rest += part
            with contextlib.suppress(ValueError):
                self.requests.append(json.loads(rest.decode("utf-8")))

            reason = {200: "OK", 404: "Not Found", 500: "Internal Server Error"}[self.status]
            conn.sendall(
                f"HTTP/1.1 {self.status} {reason}\r\n"
                "Content-Type: application/x-ndjson\r\n"
                "Transfer-Encoding: chunked\r\n\r\n".encode()
            )
            if self.status != 200:
                self._chunk(conn, json.dumps({"error": "boom"}) + "\n")
                self._chunk(conn, "")
                return

            for piece in self.chunks:
                self._chunk(conn, json.dumps({"response": piece, "done": False}) + "\n")
            if self.forever:
                # Never says `done`. A model that keeps generating is exactly the case where a leak
                # costs a GPU, so it is the case the tests are built around.
                while not self._stop.is_set():
                    self._chunk(conn, json.dumps({"response": ".", "done": False}) + "\n")
                    time.sleep(0.02)
            else:
                self._chunk(conn, json.dumps({"response": "", "done": True}) + "\n")
                self._chunk(conn, "")
        except OSError:
            pass  # the peer hung up — which is the outcome under test
        finally:
            self.durations[index] = time.monotonic() - started
            with contextlib.suppress(OSError):
                conn.close()

    @staticmethod
    def _chunk(conn: socket.socket, body: str) -> None:
        data = body.encode("utf-8")
        conn.sendall(b"%x\r\n%s\r\n" % (len(data), data))

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError):
            self._sock.close()


@pytest.fixture()
def server():
    made: list[FakeOllama] = []

    def make(**kwargs: Any) -> FakeOllama:
        found = FakeOllama(**kwargs)
        made.append(found)
        return found

    yield make
    for found in made:
        found.close()
    reset_slots()


def run(coro):
    return asyncio.run(coro)


def test_it_asks_for_fill_in_the_middle_not_a_continuation(server) -> None:
    """`suffix` must be in the request, or this is autocomplete with extra steps.

    Without it the model writes the closing brace you already have and redeclares the variable on
    the next line — worse than no suggestion, because it looks right until you read past the caret.
    """
    fake = server()

    found = run(
        complete_inline("def add(a, b):\n    ", "\n\nprint(add(1, 2))\n", base_url=fake.url, model="m")
    )

    assert found.available is True
    assert found.text == "hello world"
    sent = fake.requests[0]
    assert sent["suffix"].startswith("\n\nprint(add")
    assert sent["prompt"].endswith("def add(a, b):\n    ")
    assert sent["options"]["num_predict"] > 0


def test_a_superseded_request_hangs_up_instead_of_finishing(server) -> None:
    """The one that matters.

    A keystroke supersedes the request before it. If the superseded one keeps streaming, a local GPU
    spends its time on text nobody will read and the NEXT request queues behind it — and the symptom
    is "the model is slow", which sends you tuning the model instead of fixing the leak. Closing the
    connection is how Ollama is told to stop, so the assertion is about the connection, not the text.
    """
    fake = server(forever=True)

    async def scenario() -> tuple[Any, Any]:
        # A five-second budget on the FIRST one, deliberately. If nothing supersedes it, it streams
        # for five seconds — so the assertion below separates "cancelled" from "timed out", which a
        # shared budget could not: with both at 600ms, a leak and a cancel look identical.
        first = asyncio.create_task(
            complete_inline("a", "", base_url=fake.url, model="m", key="tab", budget_ms=5000)
        )
        await asyncio.sleep(0.2)  # let it connect and start streaming
        second = await complete_inline(
            "ab", "", base_url=fake.url, model="m", key="tab", budget_ms=300
        )
        return await first, second

    started = time.monotonic()
    _first, second = run(scenario())
    took = time.monotonic() - started

    assert fake.durations.get(0, 99) < 1.0, (
        f"the superseded connection stayed open {fake.durations.get(0)}s — the model kept generating"
    )
    assert took < 2.0, "the scenario waited out the first request's own budget"
    assert second.available is True


def test_a_slow_model_is_cut_at_the_budget(server) -> None:
    # Past the budget the suggestion has stopped being a suggestion and become an interruption.
    fake = server(forever=True)

    started = time.monotonic()
    found = run(complete_inline("x", "", base_url=fake.url, model="m", budget_ms=200))
    took = time.monotonic() - started

    assert found.text == ""
    assert took < 1.5, f"the budget did not cut the request ({took:.2f}s)"
    assert found.available is True, "a slow answer is not a broken configuration"


def test_a_missing_model_says_how_to_get_it(server) -> None:
    """`available: false` plus the pull command. "Unavailable" without a remedy is a shrug, and a
    feature that is silently off is indistinguishable from one that is broken."""
    fake = server(status=404)

    found = run(complete_inline("x", "", base_url=fake.url, model="qwen2.5-coder:1.5b-base"))

    assert found.available is False
    assert "ollama pull qwen2.5-coder:1.5b-base" in found.note


def test_no_server_at_all_is_a_sentence_not_an_exception() -> None:
    # Port 1 is never listening. A machine without Ollama is a normal state for this feature.
    found = run(complete_inline("x", "", base_url="http://127.0.0.1:1", model="m"))

    assert found.available is False
    assert "127.0.0.1:1" in found.note


def test_a_suggestion_that_restates_the_suffix_is_trimmed(server) -> None:
    # A base model often re-emits the text after the cursor, which would show you your own file as
    # a suggestion — and accepting it would duplicate the line.
    fake = server(chunks=["    return a + b\n", "print(add(1, 2))"])

    found = run(complete_inline("def add(a, b):\n", "print(add(1, 2))\n", base_url=fake.url, model="m"))

    assert found.text == "    return a + b"


def test_an_empty_document_does_not_start_a_request(server) -> None:
    # Nothing to fill in the middle of. Cheap, and it keeps the ledger's denominator meaningful.
    fake = server()

    found = run(complete_inline("   ", "  \n", base_url=fake.url, model="m"))

    assert found.text == ""
    assert found.available is True
    assert fake.requests == []
