"""Ollama's default context here is 4,096 tokens and it keeps only half of ``num_ctx`` for prompts.
A 30,314-token prompt yielded 2,050 tokens by default and 8,194 with ``num_ctx=16384``. Prose is
about 5.5 characters per token, so the default reads roughly 11,000 characters. That is under the
14,000-character verified-answers cap, so long attachments can be silently truncated."""

from __future__ import annotations

import json

import httpx

from chimera.decisions.contract import Choice, Decider
from chimera.decisions.local import NUM_CTX, PROMPT_BUDGET, LocalLogprobBackend

QUESTION = Choice(key="k", instructions="Which one?", options=("alpha", "beta"))


def _backend(prompt_eval_count: int) -> LocalLogprobBackend:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"details": {}})
        if request.url.path == "/api/chat":
            return httpx.Response(
                200,
                json={
                    "message": {"content": json.dumps({"k": "alpha"})},
                    "prompt_eval_count": prompt_eval_count,
                    "logprobs": [],
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return LocalLogprobBackend("http://ollama.test", client=client)


def test_every_call_sets_the_context_window() -> None:
    backend = _backend(prompt_eval_count=900)

    assert backend.body("state", QUESTION)["options"]["num_ctx"] == NUM_CTX


def test_a_state_that_filled_the_window_is_a_halt_not_an_answer() -> None:
    backend = _backend(prompt_eval_count=PROMPT_BUDGET + 2)

    answer = Decider(backend).decide("t", "state", QUESTION)

    assert answer.halt is not None
    assert "context" in answer.halt
    assert answer.p is None


def test_a_state_under_the_window_is_read_normally() -> None:
    backend = _backend(prompt_eval_count=900)

    answer = Decider(backend).decide("t", "state", QUESTION)

    assert answer.halt is None
