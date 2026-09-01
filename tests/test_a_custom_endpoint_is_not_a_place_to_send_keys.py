"""`CHIMERA_API_BASE` points every call somewhere else, and every key is already in the environment.

`_export_keys_to_env` puts every configured provider key into `os.environ` so LiteLLM can see them.
`_provider_kwargs` applies `settings.api_base` — a single global — to every call. Set the base to a
third-party OpenAI-compatible endpoint with `OPENAI_API_KEY` present, and the OpenAI key travels
there. Nothing warns.

This is a footgun, not an exploit: the person set the value. The field's own comment says what it is
for — *"self-hosted/OpenAI-compatible servers (Ollama, vLLM)"* — which is the loopback case, and for
that case nothing changes here. What changes is that a plain-HTTP base pointing at a host that is
not this machine is refused, because sending a bearer token over cleartext to somewhere else is the
one version of this that has no legitimate reading.

The host is compared after parsing, never by substring. `"localhost" in url` accepts
`http://evil.localhost.com`, and a check that can be defeated by naming a domain is not a check.
"""

from __future__ import annotations

import pytest

from chimera.config import Settings


def _base(url: str) -> Settings:
    return Settings(CHIMERA_API_BASE=url)  # type: ignore[call-arg]


PERMITIDOS = [
    ("http://127.0.0.1:11434", "Ollama, o caso que o campo existe para servir"),
    ("http://localhost:8000/v1", "vLLM local"),
    ("http://[::1]:8080", "loopback IPv6"),
    ("https://meu-gateway.exemplo.com/v1", "um endpoint remoto, mas sob TLS"),
    ("HTTPS://MAIUSCULO.EXEMPLO.COM/v1", "esquema em maiúsculas ainda é TLS"),
]

RECUSADOS = [
    ("http://gateway-de-terceiro.com/v1", "texto claro para outra máquina"),
    ("http://evil.localhost.com/v1", "o domínio que derrota `\"localhost\" in url`"),
    ("http://127.0.0.1.atacante.com/v1", "o mesmo truque com o endereço"),
    ("HTTP://GATEWAY.COM/v1", "esquema em maiúsculas não escapa da regra"),
]


@pytest.mark.parametrize(("url", "porque"), PERMITIDOS)
def test_a_legitimate_base_is_accepted(url: str, porque: str) -> None:
    assert _base(url).api_base == url, porque


@pytest.mark.parametrize(("url", "porque"), RECUSADOS)
def test_cleartext_to_somewhere_else_is_refused(url: str, porque: str) -> None:
    with pytest.raises(ValueError, match="CHIMERA_API_BASE"):
        _base(url)


def test_the_refusal_says_what_to_do() -> None:
    """A rejected setting that does not say why is a setting somebody works around by deleting the
    guard. The two legitimate answers are `https://` or a loopback address, and the message says
    both."""
    with pytest.raises(ValueError) as erro:
        _base("http://gateway-de-terceiro.com/v1")

    mensagem = str(erro.value)
    assert "https" in mensagem
    assert "localhost" in mensagem or "127.0.0.1" in mensagem


def test_no_base_is_still_the_default() -> None:
    """Unset is the ordinary case and must stay untouched — this guard exists for a value somebody
    typed, and typing nothing is not a value."""
    assert Settings().api_base is None


def test_a_malformed_url_is_refused_rather_than_assumed_local() -> None:
    """The safe default when parsing fails. Treating an unparseable base as loopback would let a
    string that no parser understands past a check whose whole job is to decide where it points."""
    with pytest.raises(ValueError, match="CHIMERA_API_BASE"):
        _base("http://[isto-nao-e-um-host")
