"""A dropped connection during an install, and what happens next.

A skill is N files and each is its own connection, so a link that drops a fraction of them does not
fail a fraction of installs — it fails almost all of the large ones. Measured on one real machine
while installing from the catalogue: `raw.githubusercontent.com` reset 17% of connections in one
sample and 50% in another, while `api.github.com` reset none in the same minutes.

At 17% independent loss that is 83% of installs completing at one file, 69% at two, 23% at eight,
and effectively zero at the 54-file skill in this catalogue — which is exactly the pattern observed:
the one-file and two-file skills installed, the eight-file and 54-file ones never did. And the error
said the source could not be reached, which was true of that one connection and not of anything the
user could act on.

Spacing the requests was measured too, and made it worse rather than better — so this is loss, not
rate limiting, and the fix is another attempt rather than a slower one.

**Only transport failures retry.** A 404 will be a 404 next time, and repeating a 403 spends the
same anonymous budget that produced it.
"""

from __future__ import annotations

import urllib.error
from typing import Any

import pytest

import chimera.skills.bundles as bundles
from chimera.skills.bundles import FETCH_ATTEMPTS, BundleError


@pytest.fixture(autouse=True)
def sem_espera(monkeypatch: pytest.MonkeyPatch) -> None:
    """No real sleeping in a unit test."""
    monkeypatch.setattr(bundles, "FETCH_BACKOFF_S", 0.0)


class _Resposta:
    """The bit of an HTTP response `_get_once` touches."""

    def __init__(self, corpo: bytes) -> None:
        self._corpo = corpo

    def read(self, _n: int | None = None) -> bytes:
        return self._corpo

    def __enter__(self) -> _Resposta:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


def _abrir(monkeypatch: pytest.MonkeyPatch, roteiro: list[Exception | bytes]) -> list[int]:
    """Script `urlopen` itself, so the REAL `_get_once` runs.

    Patching `_get_once` wholesale was the first version of this, and it tested nothing: the
    decision about which failures are transport failures lives inside that function, so replacing
    it removed the half of the contract most worth checking.
    """
    chamadas: list[int] = []

    def falso(_req: Any, **_kw: Any) -> Any:
        i = len(chamadas)
        chamadas.append(1)
        passo = roteiro[min(i, len(roteiro) - 1)]
        if isinstance(passo, Exception):
            raise passo
        return _Resposta(passo)

    monkeypatch.setattr(bundles.urllib.request, "urlopen", falso)
    return chamadas


def _queda() -> Exception:
    return urllib.error.URLError(OSError(10054, "connection reset by peer"))


def _http(codigo: int) -> Exception:
    return urllib.error.HTTPError("https://x/y", codigo, "no", {}, None)  # type: ignore[arg-type]


def test_one_dropped_connection_does_not_fail_the_install(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas = _abrir(monkeypatch, [_queda(), b"conteudo"])

    assert bundles._get("https://raw.githubusercontent.com/o/r/main/f") == b"conteudo"
    assert len(chamadas) == 2, "it gave up on the first drop"


def test_it_gives_up_rather_than_retrying_forever(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas = _abrir(monkeypatch, [_queda()])

    with pytest.raises(BundleError) as erro:
        bundles._get("https://raw.githubusercontent.com/o/r/main/f")

    assert len(chamadas) == FETCH_ATTEMPTS
    # The message says it already tried, so nobody retries by hand what was retried for them.
    assert str(FETCH_ATTEMPTS) in str(erro.value)


def test_a_missing_file_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 will be a 404 next time. Retrying it turns a clear answer into a slow clear answer."""
    chamadas = _abrir(monkeypatch, [_http(404)])

    with pytest.raises(BundleError):
        bundles._get("https://raw.githubusercontent.com/o/r/main/f")

    assert len(chamadas) == 1, "a 404 was asked for more than once"


def test_a_refusal_on_the_hourly_limit_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeating a 403 spends the same budget that caused it, and makes the wait longer."""
    chamadas = _abrir(monkeypatch, [_http(403)])

    with pytest.raises(BundleError) as erro:
        bundles._get("https://api.github.com/repos/o/r")

    assert len(chamadas) == 1
    # And it still names the cause, which is the one thing the user can act on.
    assert "limit" in str(erro.value).lower()


def test_a_response_over_the_size_cap_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """`BundleError` from the size guard is an answer, not a transport failure. Asking again would
    download the same too-large file two more times."""
    # A body over the cap: the real `_get_once` raises BundleError for it, and that is an answer
    # rather than a transport failure — asking again would download it twice more.
    #
    # The limit is passed rather than patched: `MAX_FILE_BYTES` is a parameter DEFAULT, bound when
    # the function was defined, so setting the module attribute changes nothing the call can see.
    chamadas = _abrir(monkeypatch, [b"muito grande para o teto"])

    with pytest.raises(BundleError):
        bundles._get("https://raw.githubusercontent.com/o/r/main/f", limit=4)

    assert len(chamadas) == 1
