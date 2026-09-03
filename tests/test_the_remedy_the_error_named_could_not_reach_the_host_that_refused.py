"""Installing a skill died on a 429 and blamed a limit that was not the one that bit.

Measured while installing two skills from the catalogue in a row, on a machine with 55 of the 60
anonymous API calls still unspent: the install failed and said *"GitHub refused the request — most
likely its hourly limit for anonymous downloads. Try again later, or set GITHUB_TOKEN."*

Three things were wrong with that, and none of them raised so much as a warning:

* The refusal came from ``raw.githubusercontent.com``, which is where the files are fetched one per
  request — not from ``api.github.com``, whose hourly limit the message describes.
* Setting ``GITHUB_TOKEN``, the remedy it prints, **could not affect the failing request**: the
  header was attached only when the host was ``api.github.com``. The advice was inert for the only
  failure that ever printed it.
* HTTP 429 means *ask again later*. It was handled beside 403 as a flat refusal, so the one status
  that carries its own remedy — often with a ``Retry-After`` naming the wait — was the one status
  never retried.

The tests drive the module's own fetch path with a stub opener, so a revert of any single fix fails
here rather than needing a live host and a real throttle to reproduce.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from chimera.skills import bundles

RAW = "https://raw.githubusercontent.com/o/r/main/skills/x/SKILL.md"
API = "https://api.github.com/repos/o/r/git/trees/main:skills/x"


def _http_error(url: str, code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    """An HTTPError shaped like the real one, headers included."""
    import email.message

    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(url, code, "throttled", headers, io.BytesIO(b"rate-limited"))


class _Resposta:
    """The context-manager shape `urlopen` returns."""

    def __init__(self, corpo: bytes) -> None:
        self._corpo = corpo

    def read(self, _n: int = -1) -> bytes:
        return self._corpo

    def __enter__(self) -> _Resposta:
        return self

    def __exit__(self, *_: object) -> bool:
        return False


@pytest.fixture
def espiao(monkeypatch: pytest.MonkeyPatch):
    """Capture every request the module makes, and script the answers by attempt number."""
    pedidos: list[urllib.request.Request] = []
    respostas: list[object] = []

    def falso_urlopen(req: urllib.request.Request, timeout: float = 0) -> object:
        pedidos.append(req)
        proxima = respostas.pop(0) if respostas else _Resposta(b"corpo")
        if isinstance(proxima, Exception):
            raise proxima
        return proxima

    monkeypatch.setattr(bundles.urllib.request, "urlopen", falso_urlopen)
    monkeypatch.setattr(bundles.time, "sleep", lambda _s: None)  # no real waiting in a test
    return pedidos, respostas


# --------------------------------------------------------------------------------------------
# 1. The remedy the message names must reach the host that refused


def test_the_token_reaches_the_host_that_actually_serves_the_files(
    espiao, monkeypatch: pytest.MonkeyPatch
) -> None:
    pedidos, _ = espiao
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")

    bundles._get_once(RAW, accept="*/*")

    enviado = pedidos[-1].get_header("Authorization")
    assert enviado == "Bearer t0ken", (
        "the files are fetched from raw.githubusercontent.com, so a token that never goes there "
        "cannot help the request that fails — which is the request the message blames"
    )


def test_the_token_still_reaches_the_api_host(espiao, monkeypatch: pytest.MonkeyPatch) -> None:
    pedidos, _ = espiao
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")

    bundles._get_once(API)

    assert pedidos[-1].get_header("Authorization") == "Bearer t0ken"


def test_no_token_means_no_header(espiao, monkeypatch: pytest.MonkeyPatch) -> None:
    pedidos, _ = espiao
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    bundles._get_once(RAW, accept="*/*")

    assert pedidos[-1].get_header("Authorization") is None


def test_credentials_go_nowhere_but_the_two_allowlisted_hosts() -> None:
    """The widening is a correction, not a hole: the check is the allowlist itself."""
    fonte = Path(bundles.__file__).read_text(encoding="utf-8")
    assert 'if token and parsed.hostname in _ALLOWED_HOSTS:' in fonte
    assert bundles._ALLOWED_HOSTS == ("api.github.com", "raw.githubusercontent.com")


# --------------------------------------------------------------------------------------------
# 2. A 429 is a request to wait, so it is retried


def test_a_429_is_retried_and_can_succeed(espiao) -> None:
    _, respostas = espiao
    respostas.extend([_http_error(RAW, 429), _Resposta(b"o arquivo")])

    assert bundles._get(RAW, accept="*/*") == b"o arquivo", (
        "429 means ask again later; failing on the first one throws away the answer the server gave"
    )


def test_a_403_is_not_retried(espiao) -> None:
    """Repeating a 403 spends the same budget that caused it — the distinction is the point."""
    pedidos, respostas = espiao
    respostas.extend([_http_error(RAW, 403), _Resposta(b"nunca chega aqui")])

    with pytest.raises(bundles.BundleError):
        bundles._get(RAW, accept="*/*")
    assert len(pedidos) == 1


def test_a_429_that_never_clears_still_fails_with_a_readable_message(espiao) -> None:
    pedidos, respostas = espiao
    respostas.extend([_http_error(RAW, 429) for _ in range(bundles.THROTTLE_ATTEMPTS)])

    with pytest.raises(bundles.BundleError) as capturado:
        bundles._get(RAW, accept="*/*")
    assert "raw.githubusercontent.com" in str(capturado.value)
    assert len(pedidos) == bundles.THROTTLE_ATTEMPTS, "give up after the budget, not before it"


def test_throttling_has_its_own_budget_and_does_not_spend_the_transport_one(espiao) -> None:
    """A 55-file install meets the throttle around the 13th file; three tries never finish it.

    Measured against `raw.githubusercontent.com` with distinct files: the 429 lands somewhere near
    the thirteenth request and the bucket refills within tens of seconds. Sharing one counter with
    dropped-socket retries is what made a big skill unfinishable.
    """
    _, respostas = espiao
    respostas.extend([_http_error(RAW, 429) for _ in range(bundles.FETCH_ATTEMPTS)])
    respostas.append(_Resposta(b"chegou"))

    assert bundles._get(RAW, accept="*/*") == b"chegou"
    assert bundles.THROTTLE_ATTEMPTS > bundles.FETCH_ATTEMPTS


def test_the_waits_grow_when_the_server_names_no_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a Retry-After, back off exponentially — a flat pause re-enters the same burst."""
    dormidas: list[float] = []
    respostas: list[object] = [_http_error(RAW, 429) for _ in range(3)] + [_Resposta(b"ok")]

    def urlopen(req: urllib.request.Request, timeout: float = 0) -> object:
        proxima = respostas.pop(0)
        if isinstance(proxima, Exception):
            raise proxima
        return proxima

    monkeypatch.setattr(bundles.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(bundles.time, "sleep", dormidas.append)

    bundles._get(RAW, accept="*/*")

    assert dormidas == [2.0, 4.0, 8.0], f"esperava a dobra, veio {dormidas}"


def test_pacing_was_tried_and_is_deliberately_not_shipped() -> None:
    """The docstring records a refuted intervention; the code must match what it says.

    Spacing the per-file requests was implemented, measured, and removed: at 0.15s the throttle
    arrived on the 2nd request against the 13th with no pause at all. That comparison is confounded
    (consecutive probes share one bucket), which is exactly why it cannot be shipped as though it
    worked. This guards the pair — the note stays true and the constant stays gone.
    """
    fonte = Path(bundles.__file__).read_text(encoding="utf-8")
    assert not hasattr(bundles, "FILE_PACING_S")
    assert "Spacing the requests was tried first and is deliberately not here" in fonte


def test_the_wait_the_server_asked_for_is_the_wait_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    dormidas: list[float] = []
    respostas: list[object] = [_http_error(RAW, 429, retry_after="7"), _Resposta(b"ok")]

    def urlopen(req: urllib.request.Request, timeout: float = 0) -> object:
        proxima = respostas.pop(0)
        if isinstance(proxima, Exception):
            raise proxima
        return proxima

    monkeypatch.setattr(bundles.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(bundles.time, "sleep", dormidas.append)

    bundles._get(RAW, accept="*/*")

    assert 7.0 in dormidas, "Retry-After names the wait; guessing instead ignores what was said"


def test_an_absurd_retry_after_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ten-minute wait must surface as a message, not as an install that appears to hang."""
    dormidas: list[float] = []
    respostas: list[object] = [_http_error(RAW, 429, retry_after="600"), _Resposta(b"ok")]

    def urlopen(req: urllib.request.Request, timeout: float = 0) -> object:
        proxima = respostas.pop(0)
        if isinstance(proxima, Exception):
            raise proxima
        return proxima

    monkeypatch.setattr(bundles.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(bundles.time, "sleep", dormidas.append)

    bundles._get(RAW, accept="*/*")

    assert max(dormidas) <= bundles.THROTTLE_MAX_WAIT_S


@pytest.mark.parametrize("bruto", ["", "Wed, 21 Oct 2026 07:28:00 GMT", "abacaxi", "-5", "0"])
def test_an_unreadable_retry_after_falls_back_instead_of_raising(bruto: str) -> None:
    erro = _http_error(RAW, 429, retry_after=bruto or None)
    assert bundles._retry_after(erro, 2.0) == 2.0


# --------------------------------------------------------------------------------------------
# 3. The message names the host and the code it actually got


def test_the_message_names_the_host_and_the_status(espiao) -> None:
    _, respostas = espiao
    respostas.append(_http_error(API, 403))

    with pytest.raises(bundles.BundleError) as capturado:
        bundles._get(API)

    recado = str(capturado.value)
    assert "api.github.com" in recado
    assert "403" in recado, "blaming an hourly limit when the answer was something else misdirects"


