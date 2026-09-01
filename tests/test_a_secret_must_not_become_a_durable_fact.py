"""A secret typed after "remember that" went to disk whole, and came back inside future prompts.

`grep -rn "redact|secret|sanitiz" chimera/memory/` returned nothing. The trace redacts, the crash
reason redacts, the code session redacts — memory did not, and memory is the surface with the
longest reach: a fact written today is read back into the system prompt of every matching
conversation from now on.

Fixed at the manager rather than at the two call sites. `ChatSession` and the desktop coding turn
both parse "remember that …" and hand the text to `remember`, and any future writer would be one
forgotten call away from the same hole — the same reasoning that put the memory admission gate on
the default instead of on `code_api`.

`redact` is narrow on purpose and that constraint is louder here than anywhere else: a memory this
function mangles is a fact the agent will act on, forever, in a form nobody wrote. Most of the
weight below is on what must survive untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.memory.manager import MemoryManager
from chimera.memory.store import MemoryStore


@pytest.fixture
def memoria(tmp_path: Path) -> MemoryManager:
    return MemoryManager(MemoryStore(tmp_path / "m.json"))


SEGREDOS = [
    ("a chave da produção é sk-proj-AAAAAAAAAAAAAAAAAAAAAA", "sk-proj-AAAAAAAAAAAAAAAAAAAAAA"),
    ("o token é ghp_" + "B" * 30, "ghp_" + "B" * 30),
    ("o banco fica em postgresql://postgres:senhaReal123@db.co:5432/x", "senhaReal123"),
    ("o webhook é https://discord.com/api/webhooks/123/aBcDeF-gH1jKlMnOpQrStUvW",
     "aBcDeF-gH1jKlMnOpQrStUvW"),
    ("chame com Authorization: Bearer AbCdEfGhIjKlMnOpQrStUvWxYz012345", "AbCdEfGhIjKlMnOpQrStUvWxYz012345"),
]


@pytest.mark.parametrize(("texto", "segredo"), SEGREDOS)
def test_a_secret_never_reaches_the_store(memoria: MemoryManager, texto: str, segredo: str) -> None:
    _op, item = memoria.remember(texto, source="chat")

    assert segredo not in item.content
    assert all(segredo not in guardado.content for guardado in memoria.store.all())


def test_the_fact_is_still_a_fact(memoria: MemoryManager) -> None:
    """Masking must leave something worth recalling. "the production key is [redacted]" is a useful
    memory — it says a key exists and where it belongs. `[redacted]` alone is not."""
    _op, item = memoria.remember("a chave da produção é sk-proj-AAAAAAAAAAAAAAAAAAAAAA")

    assert "chave da produção" in item.content
    assert len(item.content) > 20


# --------------------------------------------------------------- what must survive


ORDINARIOS = [
    "o projeto usa Postgres na porta 5432",
    "o Bruno prefere respostas em português",
    "a reunião de segunda é às 9h",
    "o repositório é github.com/brcampidelli/chimera-agent",
    "rodar os testes com `python -m pytest tests/ -q`",
    "o build quebra quando o Node é menor que 20",
]


@pytest.mark.parametrize("texto", ORDINARIOS)
def test_an_ordinary_fact_is_stored_verbatim(memoria: MemoryManager, texto: str) -> None:
    """The constraint that matters most on this surface. A memory this mangles is a fact the agent
    acts on, forever, in a form nobody wrote — and unlike a trace, nobody re-reads it to notice."""
    _op, item = memoria.remember(texto)

    assert item.content == texto


# --------------------------------------------------------------- the machinery still works


def test_dedup_still_sees_two_writes_of_the_same_secret_as_one(memoria: MemoryManager) -> None:
    """Masking happens before the duplicate check, or the same fact written twice would land twice —
    identical after masking, different before it."""
    memoria.remember("a chave é sk-proj-AAAAAAAAAAAAAAAAAAAAAA")
    op, _item = memoria.remember("a chave é sk-proj-AAAAAAAAAAAAAAAAAAAAAA")

    assert op == "NOOP"


def test_taint_still_travels(memoria: MemoryManager) -> None:
    """The provenance rule is untouched: a fact learned during a run that read untrusted content is
    still marked, and masking must not launder that."""
    _op, item = memoria.remember("algo aprendido", provenance="tainted")

    assert item.provenance == "tainted"
