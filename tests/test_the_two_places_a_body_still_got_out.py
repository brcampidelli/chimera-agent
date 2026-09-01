"""The two surfaces that still carried a provider's answer out: one to disk, one to the screen.

A failed tool call reports back the provider's own error body, and that body has been measured to
carry an echoed fragment of the prompt and the provider's internal routing trace. `steplog.py` has
written through `redact` since it was born. These two never picked it up:

- the **code session store**, which keeps every message of a conversation, tool observations
  included, in a file that outlives the session;
- the **fusion panel error**, which rides `route_meta` out to the desktop app and to
  `/v1/chat/completions` — and was the only error surface in the app with no length limit at all.

The session store is the delicate one, because it is read BACK to continue a conversation. The tests
below spend most of their weight there: masking a transcript must not stop it being a transcript.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.core.code_session import CodeSession, CodeSessionStore
from chimera.core.redact import MASK

CHAVE = "sk-or-v1-thisisaverylongfakekeyvalue"


class _AgentMudo:
    """A code agent that is never called: these tests are about what is stored, not what is said."""

    def run(self, *_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover - nunca chamado
        raise AssertionError("o agente nao deveria rodar neste teste")


def _sessao(mensagens: list[dict[str, str]]) -> CodeSession:
    return CodeSession(_AgentMudo(), session_id="s1", workspace="/tmp/w", messages=list(mensagens))


# --------------------------------------------------------------------- a transcricao em disco


def test_a_failed_tool_does_not_leave_the_providers_body_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shape the audit actually found: `agent.py` turns a tool exception into an observation, and
    the observation is a message like any other."""
    monkeypatch.setenv("OPENROUTER_API_KEY", CHAVE)
    loja = CodeSessionStore(tmp_path)
    loja.save(
        _sessao(
            [
                {"role": "user", "content": "arrume o build"},
                {"role": "tool", "content": f"error: tool 'complete' failed: 401 - Bearer {CHAVE}"},
            ]
        )
    )
    bruto = (tmp_path / "s1.json").read_text(encoding="utf-8")
    assert CHAVE not in bruto
    assert MASK in bruto


def test_the_conversation_survives_being_masked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The assertion that decides whether this change is allowed to ship.

    This store is read back to continue a session. If masking broke the round-trip — a file that no
    longer parses, messages that lost their roles, a workspace that vanished — the fix would trade a
    leak for a broken product.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", CHAVE)
    loja = CodeSessionStore(tmp_path)
    mensagens = [
        {"role": "user", "content": "explique este arquivo"},
        {"role": "assistant", "content": "ele monta a API"},
        {"role": "tool", "content": f"error: 401 - Bearer {CHAVE}"},
    ]
    loja.save(_sessao(mensagens))

    voltou = loja.load("s1", _AgentMudo())
    assert voltou.workspace == "/tmp/w"
    assert len(voltou.messages) == 3
    assert [m["role"] for m in voltou.messages] == ["user", "assistant", "tool"]
    assert voltou.messages[0]["content"] == "explique este arquivo"
    assert voltou.messages[1]["content"] == "ele monta a API"
    assert CHAVE not in json.dumps(voltou.messages)


def test_ordinary_code_passes_through_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure mode a greedy redactor would have: masking the work itself.

    `redact` is narrow on purpose — six credential shapes plus this process's own environment
    secrets — and its own docstring says why: a pattern that redacted ordinary output would make the
    file useless. This pins that, because widening the patterns later would break it here first.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", CHAVE)
    codigo = (
        "def somar(a: int, b: int) -> int:\n"
        "    # sk é um prefixo curto, sk-1 não é uma chave, e este hash não é um token:\n"
        "    hash = 'a1b2c3d4e5f6'\n"
        "    return a + b\n"
    )
    loja = CodeSessionStore(tmp_path)
    loja.save(_sessao([{"role": "assistant", "content": codigo}]))
    assert loja.load("s1", _AgentMudo()).messages[0]["content"] == codigo


def test_a_session_with_no_secret_is_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing to mask means nothing changes — the guard has no cost in the ordinary case."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    loja = CodeSessionStore(tmp_path)
    mensagens = [{"role": "user", "content": "oi"}, {"role": "assistant", "content": "olá"}]
    loja.save(_sessao(mensagens))
    assert json.loads((tmp_path / "s1.json").read_text(encoding="utf-8"))["messages"] == mensagens


# --------------------------------------------------------------------- o erro do painel, servido


def _painel_com_erro(mensagem: str) -> str:
    """Run one panel model that raises, and return the error string that would be served."""
    from chimera.fusion.engine import FusionEngine

    class _Explode:
        def complete(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError(mensagem)

    engine = FusionEngine(_Explode())
    respostas = engine._run_panel([{"role": "user", "content": "oi"}], ["m1"])  # noqa: SLF001
    assert respostas[0].error is not None
    return respostas[0].error


def test_the_panel_error_does_not_carry_a_secret_to_the_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This one is *served* — `route_meta` reaches the desktop app and `/v1/chat/completions`."""
    monkeypatch.setenv("OPENROUTER_API_KEY", CHAVE)
    assert CHAVE not in _painel_com_erro(f"401 - Bearer {CHAVE}")


def test_the_panel_error_is_bounded() -> None:
    """It was the only error surface in the app with no limit at all, one message per panel model."""
    assert len(_painel_com_erro("y" * 4000)) <= 200


def test_the_panel_error_still_says_which_failure_it_was() -> None:
    """Bounding must not turn a diagnosable trace into a shrug. Reducing this to a `FailoverReason`
    would have flattened five distinct failures into one word — which is why it stays text."""
    erro = _painel_com_erro("rate limit exceeded for this account")
    assert "rate limit exceeded" in erro
