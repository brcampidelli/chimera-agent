"""What survives a failed provider call: the identifiers, the category, and nothing we sent.

Three changes, one subject. When a completion fails, three things should be true and none of them
were: the reason should be decided from a fact rather than from prose, an empty account should be
told apart from a busy one, and the row that reaches disk should not carry the provider's answer
back verbatim.

The last one is the reason this file exists. A failed completion arrives as a LiteLLM exception whose
message *is* the upstream response body, and that body has been measured to carry an echoed fragment
of the prompt and the provider's internal routing trace. LiteLLM masks `Bearer …` on the way in, so
the key is not the exposure — the user's own content is. `steplog.py` has written through `redact`
for exactly this reason since it was born; the crashed-run row in `app.py` was written by hand and
never picked it up.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.providers.failover import (
    CredentialPool,
    FailoverReason,
    ProviderTrace,
    action_for,
    classify,
    status_of,
    trace_of,
)


class _Headers(dict[str, str]):
    """Just enough of a header bag: case-insensitive `get`, like every HTTP library's."""

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        for name, value in self.items():
            if name.lower() == key.lower():
                return value
        return default


class _Response:
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self.headers = _Headers(headers or {})


class _ProviderError(Exception):
    """Shaped like the LiteLLM errors the gateway actually catches: a status and a response."""

    def __init__(
        self, message: str, *, status: int | None = None, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        if status is not None:
            self.status_code = status
            self.response = _Response(status, headers)


# --------------------------------------------------------------------------- status over prose


@pytest.mark.parametrize(
    ("status", "esperado"),
    [
        (401, FailoverReason.AUTH),
        (403, FailoverReason.AUTH),
        (402, FailoverReason.NO_CREDIT),
        (429, FailoverReason.RATE_LIMIT),
        (502, FailoverReason.OVERLOADED),
        (503, FailoverReason.OVERLOADED),
        (504, FailoverReason.OVERLOADED),
    ],
    ids=lambda v: str(v),
)
def test_the_status_decides_even_when_the_words_are_new(
    status: int, esperado: FailoverReason
) -> None:
    """A provider rewrites its error prose without telling anyone; it does not rewrite the protocol.

    The message here says nothing a substring match would recognise — which is the point. Measured
    against the real LiteLLM classes the prose path holds up better than expected, because the class
    NAME carries most of it, but `ServiceUnavailableError` and `InternalServerError` do fall through
    to UNKNOWN when the wording changes, and with them the action flips from `fallback_model` to
    `rotate_key`: a provider outage would rotate keys instead of changing model.
    """
    assert (
        classify(_ProviderError("something the provider decided to say today", status=status))
        is esperado
    )


def test_without_a_status_the_words_still_work() -> None:
    """The prose path is not replaced, it is only outranked. A transport-level error carries no
    status at all, and everything that worked before this change has to keep working."""
    assert classify(_ProviderError("Error code: 401 - invalid api key")) is FailoverReason.AUTH
    assert classify(_ProviderError("request timed out")) is FailoverReason.TIMEOUT
    assert (
        classify(_ProviderError("maximum context length is 8192"))
        is FailoverReason.CONTEXT_OVERFLOW
    )


def test_a_status_we_cannot_read_unambiguously_falls_through_to_the_words() -> None:
    """400 is a context overflow on one provider and a policy block on another, and 404 can be a bad
    model or a bad route. Mapping those from the number would be inventing certainty."""
    assert (
        classify(_ProviderError("maximum context length exceeded", status=400))
        is FailoverReason.CONTEXT_OVERFLOW
    )
    assert (
        classify(_ProviderError("not a valid model id", status=404))
        is FailoverReason.MODEL_NOT_FOUND
    )


def test_a_bool_is_not_a_status() -> None:
    """`True` is an `int` in Python, and an object that sets `status_code = True` would otherwise be
    read as HTTP 1."""
    exc = _ProviderError("boom")
    exc.status_code = True  # type: ignore[attr-defined]
    assert status_of(exc) is None


# --------------------------------------------------------------------------- empty vs busy


def test_an_empty_account_is_not_a_busy_one() -> None:
    """The two need different sentences from the user, and the wrong one costs them the night: a
    rate limit clears on its own, an empty balance never does."""
    assert classify(_ProviderError("Insufficient credits", status=402)) is FailoverReason.NO_CREDIT
    assert classify(_ProviderError("rate limit exceeded", status=429)) is FailoverReason.RATE_LIMIT


@pytest.mark.parametrize(
    "frase",
    [
        "Insufficient credits. Add more using the settings page",
        "insufficient_quota: You exceeded your current quota",
        "Your account has a negative credit balance",
        "billing hard limit has been reached",
    ],
)
def test_the_words_for_no_money_are_read_before_the_words_for_wait(frase: str) -> None:
    """`insufficient_quota` contains "quota", which the rate-limit branch matches. Without ordering
    these first, the most likely failure on a prepaid account reads as "wait a minute"."""
    assert classify(_ProviderError(frase)) is FailoverReason.NO_CREDIT


def test_the_digits_402_in_a_message_are_not_a_payment_problem() -> None:
    """The trap I set for myself and then avoided: `402` as a bare substring matches model ids and
    token counts. The status path is the only place that number is read."""
    assert (
        classify(_ProviderError("model deepseek-402b returned 402 tokens"))
        is not FailoverReason.NO_CREDIT
    )


def test_an_unfunded_key_is_rested_for_longer_than_a_wrong_one() -> None:
    """The remedies differ in kind — a wrong key is fixed by pasting the right one, an empty balance
    by a payment clearing. Under UNKNOWN it was retried every thirty seconds.

    Asserted through the pool rather than by reading the table, so this tests the cooldown that is
    actually applied and not a number that might have stopped being consulted.
    """
    agora = [0.0]
    pool = CredentialPool(clock=lambda: agora[0])
    pool.penalize("sem-saldo", FailoverReason.NO_CREDIT)
    pool.penalize("chave-errada", FailoverReason.AUTH)
    pool.penalize("ocupada", FailoverReason.RATE_LIMIT)

    agora[0] = 310.0  # passou o descanso de AUTH (300s) e o de RATE_LIMIT (60s)
    disponiveis = pool.available(["sem-saldo", "chave-errada", "ocupada"])
    assert "chave-errada" in disponiveis
    assert "ocupada" in disponiveis
    assert "sem-saldo" not in disponiveis, "uma conta vazia voltou a ser tentada em cinco minutos"


def test_no_credit_still_tries_the_other_keys() -> None:
    """Not ABORT: a pool can hold keys billed to different accounts, and one may still have money.
    Aborting would give up on funds that exist."""
    assert action_for(FailoverReason.NO_CREDIT) is action_for(FailoverReason.AUTH)


# --------------------------------------------------------------------------- the identifiers


def test_the_providers_identifiers_are_kept() -> None:
    """These are what a support desk asks for, and the only part of a failed response safe to quote
    back — minted by the provider, never containing anything we sent."""
    exc = _ProviderError(
        "rate limited",
        status=429,
        headers={"X-Request-Id": "req_abc123", "CF-RAY": "8f2a-GRU", "Retry-After": "30"},
    )
    trace = trace_of(exc)
    assert trace == ProviderTrace(
        status=429, request_id="req_abc123", ray="8f2a-GRU", retry_after="30"
    )
    suffix = trace.as_suffix()
    for esperado in ("status=429", "request_id=req_abc123", "cf_ray=8f2a-GRU", "retry_after=30"):
        assert esperado in suffix


def test_an_error_with_no_headers_produces_nothing_rather_than_noise() -> None:
    """A transport failure never reached a server. An empty suffix keeps the log line clean instead
    of decorating it with four `None`s."""
    assert trace_of(_ProviderError("dns lookup failed")).as_suffix() == ""


def test_a_header_bag_that_misbehaves_does_not_take_the_run_down() -> None:
    """This runs on the failure path. Something odd in a header must not turn a provider error into
    a crash inside the error handler."""

    class _Explode:
        def get(self, _name: str, _default: Any = None) -> Any:
            raise RuntimeError("this header bag is broken")

    exc = _ProviderError("boom")
    exc.response = type("R", (), {"status_code": 500, "headers": _Explode()})()  # type: ignore[attr-defined]
    assert trace_of(exc).status == 500  # the status still made it through


def test_the_identifiers_do_not_carry_the_body() -> None:
    """The split is the whole design: the message is prose the provider wrote and may quote our
    prompt back at us; the trace is identifiers. Only the second is safe to repeat."""
    exc = _ProviderError(
        "401 - {'metadata': {'echoed_prompt': 'SEGREDO DO USUARIO'}}",
        status=401,
        headers={"X-Request-Id": "req_1"},
    )
    assert "SEGREDO" not in trace_of(exc).as_suffix()


# --------------------------------------------------------------------------- nothing we sent, on disk


def _crash_row(tmp_path: Path, cause: BaseException) -> dict[str, Any]:
    from chimera.api.app import _persist_crashed_run

    _persist_crashed_run(tmp_path, "run-1", object(), tmp_path, [], cause)
    linhas = [
        ln
        for ln in (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert linhas, "nenhuma linha gravada"
    return json.loads(linhas[-1])


def test_the_provider_body_does_not_reach_the_disk_with_a_secret_in_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one place a provider's answer is persisted. A key set in the environment is a known secret
    to the redactor, so if the body echoes it back, the row must not."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-averyrealsecretvalue")
    cause = _ProviderError(
        "401 - {'error': {'metadata': {'received_authorization': 'Bearer sk-or-v1-averyrealsecretvalue'}}}",
        status=401,
    )
    row = _crash_row(tmp_path, cause)
    assert "sk-or-v1-averyrealsecretvalue" not in row["crash_reason"]
    assert row["crashed"] is True


def test_the_row_is_redacted_before_it_is_cut_and_not_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Truncating first would leave the front half of a secret the cut split in two — which is worse
    than either, because it looks redacted.

    The padding is measured, not guessed. `_ProviderError: ` is 16 characters, so 464 more put the
    secret's first character at position 480 — twenty characters short of the 500-byte cut. Get this
    wrong in the other direction and the cut removes the whole secret on its own, and the test passes
    whichever order the code uses: that is exactly what the first version of this test did, and only
    reverting the fix revealed it.
    """
    segredo = "sk-or-v1-secretsecretsecret"  # 27 chars: 20 survive the cut, 7 fall past it
    monkeypatch.setenv("OPENROUTER_API_KEY", segredo)
    preenchimento = "x" * (480 - len("_ProviderError: "))
    bruta = f"_ProviderError: {preenchimento}{segredo}"

    # The precondition, asserted on the INPUT. Checking the output length instead would fail against
    # correct code, because masking makes the row shorter than the cut — measured: 490 when redacted
    # first, 500 when cut first.
    assert len(bruta) > 500 and bruta.index(segredo) < 500 < bruta.index(segredo) + len(segredo)

    row = _crash_row(tmp_path, _ProviderError(f"{preenchimento}{segredo}"))
    assert "sk-or-v1-secret" not in row["crash_reason"]


def test_the_row_still_says_what_went_wrong(tmp_path: Path) -> None:
    """Redaction must not turn a diagnosable row into a blank one. The exception class and the
    non-secret part of the message are the reason anyone opens this file."""
    row = _crash_row(tmp_path, _ProviderError("the model refused the request", status=400))
    assert "_ProviderError" in row["crash_reason"]
    assert "the model refused the request" in row["crash_reason"]


def test_the_row_is_still_bounded(tmp_path: Path) -> None:
    """A provider that answers with a megabyte of JSON must not write a megabyte into runs.jsonl."""
    row = _crash_row(tmp_path, _ProviderError("y" * 5000))
    assert len(row["crash_reason"]) <= 500
