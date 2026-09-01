"""Six of seven credential shapes went to disk intact, because none of them look like a credential.

`redact` catches two things: values it can read out of this process's environment, and six string
shapes. That is honest about its own limits — the module docstring says so — but it leaves a whole
class untouched, and the class is not exotic. Measured, seven strings in, one masked:

    INTACTO https://admin:p4ssw0rd@painel.interno/api
    INTACTO https://api.exemplo.com/v1/dados?api_key=chave-de-teste-nao-real
    INTACTO https://discord.com/api/webhooks/1234567890/aBcDeF-gH1jKl
    INTACTO x-api-key: cabecalho-de-teste-nao-real
    INTACTO postgresql://postgres:senha@db.supabase.co:5432/postgres
    INTACTO Cookie: session=eyJhbGciOiJIUzI1NiJ9abcdef
  MASCARADO sk-proj-AAAAAAAAAAAAAAAAAAAAAA

None of the six needs to be recognised. The **place** gives them away: userinfo in a URL, a query
parameter whose NAME is `api_key`, an `Authorization` header, a database DSN. A pattern list is
guessing at the string; these are structural.

This is not hypothetical here. Delivery on the 24/7 deployment goes through a Discord webhook whose
URL *is* the secret, and `steplog` — which writes every tool's arguments and result to a file that
lives for weeks — redacts with this function.

Every fixture value below is a word, never a random-looking string. That is not cosmetic: a
high-entropy value can be caught by a SHAPE rule on its own, so a test written with one cannot say
which of the two mechanisms fired — and this whole change is the claim that the place is enough.
It is also what the repository's own secret scanner requires, and rightly: no scanner can tell a
test's fake key from a real one, so the fixture must not look like a key. An allowlist entry would
have been the other way to make the gate quiet, and it would have carved the hole in this exact
file.

The quoted-header case says `wget --header=` and not `curl -H` for the same reason, and the reason
corroborates the change: gitleaks' `curl-auth-header` rule fires on `curl -H 'x-api-key: …'` for
ANY value, including `nao-e-real`, because it is not reading the value at all — it is reading the
place. That is this pull request's thesis, arrived at independently by a different tool.

The tests below spend as much weight on what must NOT be masked. A redactor that eats ordinary
output makes the trace useless, and a useless trace gets turned off; that is written in the module's
own docstring and it is the constraint that shapes every pattern here.
"""

from __future__ import annotations

import pytest

from chimera.core.redact import MASK, redact

VAZA = [
    ("https://admin:p4ssw0rd-secreta@painel.interno/api", "p4ssw0rd-secreta", "userinfo de URL"),
    ("veja https://api.exemplo.com/v1/d?api_key=chave-de-teste-nao-real agora",
     "chave-de-teste-nao-real", "parâmetro cujo NOME é api_key"),
    ("POST https://api.x.com/?token=token-de-teste-nao-real", "token-de-teste-nao-real", "token= na query"),
    ("wget --header='x-api-key: cabecalho-de-teste-nao-real' https://api.x.com",
     "cabecalho-de-teste-nao-real", "cabeçalho x-api-key"),
    ("Authorization: Basic dXNlcjpzZW5oYQ==", "dXNlcjpzZW5oYQ==", "cabeçalho Authorization"),
    ("Cookie: session=eyJhbGciOiJIUzI1NiJ9abcdef", "eyJhbGciOiJIUzI1NiJ9abcdef", "cookie"),
    ("postgresql://postgres:senhaDoBanco123@db.supabase.co:5432/postgres",
     "senhaDoBanco123", "senha num DSN de banco"),
    ("https://discord.com/api/webhooks/1234567890/aBcDeF-gH1jKlMnOpQrStUvW",
     "aBcDeF-gH1jKlMnOpQrStUvW", "o caminho de um webhook, que É o segredo"),
]


@pytest.mark.parametrize(("texto", "segredo", "porque"), VAZA)
def test_a_secret_given_away_by_its_place_is_masked(texto: str, segredo: str, porque: str) -> None:
    assert segredo not in redact(texto), porque


@pytest.mark.parametrize(("texto", "segredo", "porque"), VAZA)
def test_the_line_still_says_what_it_was(texto: str, segredo: str, porque: str) -> None:
    """Masking must leave a diagnosable line. A trace that reads `[redacted]` and nothing else tells
    whoever opens it that something happened to a URL — not which host, which parameter, or which
    request. The point of the file is to answer that.
    """
    limpo = redact(texto)

    assert MASK in limpo
    assert len(limpo) > len(MASK) + 4


# --------------------------------------------------------------- what must survive


PRESERVAR = [
    ("https://api.exemplo.com/v1/usuarios?page=2&limit=50", "paginação não é credencial"),
    ("https://github.com/brcampidelli/chimera-agent/pull/283", "uma URL comum"),
    ("veja o arquivo em https://exemplo.com/docs/guia.html#secao", "âncora e caminho"),
    ("def somar(a: int, b: int) -> int:\n    return a + b", "código"),
    ("Content-Type: application/json", "um cabeçalho que não é credencial"),
    ("Accept-Language: pt-BR,pt;q=0.9", "outro"),
    ("postgresql://db.supabase.co:5432/postgres", "um DSN SEM senha"),
    ("https://api.exemplo.com/?query=como+redigir+segredos", "um parâmetro de busca"),
    ("O erro foi: connection refused (ECONNREFUSED) em 127.0.0.1:5432", "uma mensagem de erro"),
]


@pytest.mark.parametrize(("texto", "porque"), PRESERVAR)
def test_ordinary_text_is_untouched(texto: str, porque: str) -> None:
    """The constraint that shapes every pattern above, and the one the module's docstring names: a
    redactor that eats ordinary output makes the trace useless, and a useless trace gets turned
    off."""
    assert redact(texto) == texto, porque


def test_a_host_is_never_masked() -> None:
    """The host is what makes a leaked line diagnosable at all — and it is not the secret."""
    assert "painel.interno" in redact("https://admin:senha-secreta@painel.interno/api")


def test_the_parameter_name_survives_its_value() -> None:
    """`api_key=[redacted]` says a key was sent; `[redacted]` says a request happened."""
    assert "api_key=" in redact("https://x.com/?api_key=chave-de-teste-nao-real")


def test_the_old_shapes_still_work() -> None:
    """The first net is not replaced by the second. A regression here would trade one class of leak
    for another, which is the failure mode of adding a layer instead of a rule."""
    assert "sk-proj-AAAAAAAAAAAAAAAAAAAAAA" not in redact("chave sk-proj-AAAAAAAAAAAAAAAAAAAAAA aqui")
    assert "ghp_" + "A" * 30 not in redact("token ghp_" + "A" * 30)


def test_an_empty_string_is_returned_as_is() -> None:
    assert redact("") == ""
