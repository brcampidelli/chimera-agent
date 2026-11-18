"""The proportionality block (P1-17): off by default, one sentence's worth of difference when on.

`bench/proportionality` is the measurement. These are the properties that cost nothing and that the
bench relies on: the arms differ by the block and by nothing else, and the base prompt, the bakery
exception included, is the same bytes either way.
"""

from __future__ import annotations

from chimera.core import Agent, AgentConfig
from chimera.core.agent import DEFAULT_SYSTEM_PROMPT, PROPORTIONALITY_BLOCK
from chimera.tools.registry import ToolRegistry


class _NoBackend:
    def complete(self, *_a: object, **_k: object) -> object:  # pragma: no cover - never called
        raise AssertionError("composing a prompt must not call a model")


def _compose(**cfg: object) -> str:
    agent = Agent(_NoBackend(), ToolRegistry(), AgentConfig(prefix_nonce="", **cfg))  # type: ignore[arg-type]
    return agent.compose_system_prompt("explain")


def test_the_block_is_off_by_default() -> None:
    assert AgentConfig().proportionality is False
    assert PROPORTIONALITY_BLOCK not in _compose()


def test_on_adds_the_block_after_the_base_and_changes_nothing_else() -> None:
    off, on = _compose(proportionality=False), _compose(proportionality=True)
    assert on == off.replace(DEFAULT_SYSTEM_PROMPT, f"{DEFAULT_SYSTEM_PROMPT}\n\n{PROPORTIONALITY_BLOCK}", 1)
    assert off.startswith(DEFAULT_SYSTEM_PROMPT)


def test_the_bakery_exception_is_in_both_arms_unchanged() -> None:
    for text in (_compose(proportionality=False), _compose(proportionality=True)):
        assert "One exception, and it is deliberately narrow" in text
        assert "faz um site pra minha padaria" not in text  # the example is in a comment, not the prompt
        assert "at most three" in text


def test_the_block_keeps_a_request_to_change_something_a_task() -> None:
    """The failure this prompt was written against is answering instead of acting, so the block says
    in so many words that a change request is still carried out."""
    assert "still a task" in PROPORTIONALITY_BLOCK
    assert "carry it out with the tools" in PROPORTIONALITY_BLOCK


def test_the_env_flag_reaches_the_config(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_PROPORTIONALITY", "1")
    get_settings.cache_clear()
    try:
        assert AgentConfig().proportionality is True
    finally:
        monkeypatch.delenv("CHIMERA_PROPORTIONALITY")
        get_settings.cache_clear()
