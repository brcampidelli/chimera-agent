"""The best-built recovery in the repository does not run where the product spends its time.

`complete` walks a chain: for every candidate model, for every key in the pool, classify the failure
and decide whether to rotate the key, change the model or abort. `stream_complete` calls the provider
once, with `keys[0]`, and its docstring says so — "NO fallback chain and NO cache (both meaningless
for a live stream)".

The cache half of that is right. The fallback half is what the coding turn runs on: `code_api`
declares `stream: bool = True`, and `Agent._step` picks `stream_complete` whenever a token callback
is present. So a 429 that `complete` would have survived by rotating a key kills the turn outright,
on the one surface a person is watching.

The fix here is deliberately the small half. Once a delta has reached the client, falling back means
either replaying text the user already read or continuing from a partial answer, and neither is
obviously right — so the fallback is refused after the first token, and that refusal has its own
test. Before the first token nothing has been shown, and a retry is invisible and free.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.providers.gateway import CompletionResult, LLMGateway


class _Erro(Exception):
    """A provider error, spelled the way `classify` on this branch reads one.

    The status goes in the MESSAGE as well as on the attribute. `classify` matches on the class name
    and the text — reading the attribute first is a separate change on another branch — and a test
    that leaned on the attribute would be asserting that branch's behaviour from this one, which is
    the kind of coupling that makes a merge look like a regression.
    """

    def __init__(self, status: int, mensagem: str = "") -> None:
        super().__init__(mensagem or f"{status} provider error")
        self.status_code = status


@pytest.fixture(autouse=True)
def _com_chave(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_require_credentials` runs before the stream, so without a key nothing here reaches the
    code under test — it fails on the credential check and every assertion reads as a pass."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")


def _gateway(monkeypatch: pytest.MonkeyPatch, *, chamadas: list[str], falha: Exception | None,
             deltas: list[str] | None = None) -> LLMGateway:
    """A gateway whose streaming call fails as instructed and whose `complete` is recorded."""
    gw = LLMGateway()

    def _stream(**kwargs: Any) -> Any:
        chamadas.append("stream")
        if deltas:
            for texto in deltas:
                yield _chunk(texto)
        if falha is not None:
            raise falha

    def _complete(*_args: Any, **_kwargs: Any) -> CompletionResult:
        chamadas.append("complete")
        return CompletionResult(content="resposta do fallback", model="m")

    monkeypatch.setattr(gw, "_stream_once", _stream, raising=False)
    monkeypatch.setattr(gw, "complete", _complete)
    return gw


def _chunk(texto: str) -> Any:
    class _D:
        content = texto
        tool_calls = None

    class _C:
        delta = _D()
        finish_reason = None

    class _K:
        choices = [_C()]
        usage = None

    return _K()


# ------------------------------------------------------------------ it recovers


def test_a_rate_limit_before_the_first_token_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """The turn that used to die. Nothing was shown, so a retry costs the user nothing."""
    chamadas: list[str] = []
    resultado = _gateway(monkeypatch, chamadas=chamadas, falha=_Erro(429)).stream_complete(
        [{"role": "user", "content": "oi"}]
    )

    assert chamadas == ["stream", "complete"]
    assert resultado.content == "resposta do fallback"


def test_an_overloaded_provider_falls_back_too(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[str] = []
    _gateway(monkeypatch, chamadas=chamadas, falha=_Erro(503)).stream_complete(
        [{"role": "user", "content": "oi"}]
    )

    assert chamadas == ["stream", "complete"]


# ------------------------------------------------------------------ it refuses to


def test_a_failure_after_the_first_token_does_not_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bound, and the reason this fix is the small half.

    Once text has reached the screen, falling back means either replaying what the reader already
    read or splicing a second model's continuation onto a first model's half-sentence. Neither is
    obviously right, so the stream fails the way it always did and the caller sees the error.
    """
    chamadas: list[str] = []
    gw = _gateway(monkeypatch, chamadas=chamadas, falha=_Erro(429), deltas=["Olá"])
    vistos: list[str] = []

    with pytest.raises(_Erro):
        gw.stream_complete([{"role": "user", "content": "oi"}], on_delta=vistos.append)

    assert chamadas == ["stream"]
    assert vistos == ["Olá"]


def test_a_401_does_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad key is a reason TO retry, not a reason to stop — and this test said the opposite.

    Written asserting that a 401 aborts, on the assumption that a wrong key is wrong twice. The
    taxonomy disagrees and is right: `AUTH` maps to `ROTATE_KEY`, because a pool holds more than one
    key and the second may be fine. The streaming path used `keys[0]` and nothing else, so this is
    precisely the turn the fallback exists to save.
    """
    chamadas: list[str] = []
    _gateway(monkeypatch, chamadas=chamadas, falha=_Erro(401)).stream_complete(
        [{"role": "user", "content": "oi"}]
    )

    assert chamadas == ["stream", "complete"]


def test_an_abort_reason_does_not_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A prompt too long for the stream is too long for the batch call: same messages, same model,
    same ceiling. `CONTEXT_OVERFLOW` maps to `ABORT`, and paying for a second call that cannot
    succeed is the one thing a recovery must not do."""
    chamadas: list[str] = []
    gw = _gateway(
        monkeypatch, chamadas=chamadas, falha=_Erro(400, "maximum context length exceeded")
    )

    with pytest.raises(_Erro):
        gw.stream_complete([{"role": "user", "content": "oi"}])

    assert chamadas == ["stream"]


def test_it_only_falls_back_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fallback that could itself fall back is a loop wearing a recovery's clothes."""
    chamadas: list[str] = []
    gw = LLMGateway()

    def _stream(**_kwargs: Any) -> Any:
        chamadas.append("stream")
        raise _Erro(429)
        yield  # pragma: no cover - makes this a generator

    def _complete(*_args: Any, **_kwargs: Any) -> CompletionResult:
        chamadas.append("complete")
        raise _Erro(429)

    monkeypatch.setattr(gw, "_stream_once", _stream, raising=False)
    monkeypatch.setattr(gw, "complete", _complete)

    with pytest.raises(_Erro):
        gw.stream_complete([{"role": "user", "content": "oi"}])

    assert chamadas == ["stream", "complete"]


# ------------------------------------------------------------------ it still streams


def test_a_healthy_stream_never_reaches_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ordinary path, unchanged — and the assertion that this did not become a gateway that
    quietly answers every turn twice."""
    chamadas: list[str] = []
    gw = _gateway(monkeypatch, chamadas=chamadas, falha=None, deltas=["Olá", " mundo"])
    vistos: list[str] = []

    resultado = gw.stream_complete([{"role": "user", "content": "oi"}], on_delta=vistos.append)

    assert chamadas == ["stream"]
    assert vistos == ["Olá", " mundo"]
    assert resultado.content == "Olá mundo"
