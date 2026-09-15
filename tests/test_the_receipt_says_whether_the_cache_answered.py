"""The two switches and the two fields `bench/cache_confound` needs — and the one place the nonce may go.

arXiv:2609.04748 measured that a provider's prefix cache changes an agent's trajectory in 36.2% of
episodes, and this project engages that cache on purpose while no receipt ever said whether a run
was served from it. Checked before this file existed: 556 factorial receipts, zero cache fields.
So the durable half here is the receipt — `cache_read_tokens` and `provider` on every attempt — and
the measurement half is a nonce that defeats the cache and a temperature override that makes a
trajectory reproducible enough to blame something other than sampling.

The nonce test is the one that matters. A cache serves the longest shared prefix, so a nonce placed
after the persona would leave the persona cacheable and the "cache off" arm would be a cache-on arm
with a different name — the intervention would report itself as acting while acting on nothing.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.api.runs import build_receipt
from chimera.core.autonomous import Attempt, AutonomousResult, _cache_read_tokens, _provider


def _system_prompt_seen(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> str:
    from chimera.config import get_settings
    from chimera.core import Agent, AgentConfig

    get_settings.cache_clear()  # the env vars below must be read fresh, not from a warm cache
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    captured: dict[str, Any] = {}

    class Backend:
        def complete(self, messages: list[Any], **_: Any) -> Any:
            captured["system"] = messages[0]["content"]
            raise RuntimeError("stop — the prompt is what this test is about")

    registry = __import__("chimera.tools", fromlist=["default_registry"]).default_registry(ws)
    agent = Agent(Backend(), registry, AgentConfig(project_root=ws))
    with contextlib.suppress(Exception):
        agent.run("hi")
    get_settings.cache_clear()
    return captured["system"]


def test_without_the_switch_the_prompt_is_untouched(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHIMERA_PREFIX_NONCE", raising=False)
    system = _system_prompt_seen(tmp_path, monkeypatch)
    assert "[session " not in system


def test_the_nonce_is_the_very_first_thing_in_the_prompt(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Front, not tail. The whole point is that nothing before it can be served from a cache."""
    monkeypatch.setenv("CHIMERA_PREFIX_NONCE", "7f3a-run-1")
    system = _system_prompt_seen(tmp_path, monkeypatch)
    assert system.startswith("[session 7f3a-run-1]\n\n")


def test_temperature_reads_the_override_and_keeps_its_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.config import get_settings
    from chimera.core import AgentConfig

    monkeypatch.delenv("CHIMERA_TEMPERATURE", raising=False)
    get_settings.cache_clear()
    assert AgentConfig().temperature == 0.2
    monkeypatch.setenv("CHIMERA_TEMPERATURE", "0")
    get_settings.cache_clear()
    assert AgentConfig().temperature == 0.0
    get_settings.cache_clear()


# --------------------------------------------------------------------- the receipt


def _steplog(*steps: tuple[int | None, str]) -> Any:
    return SimpleNamespace(
        steps=[SimpleNamespace(cached_tokens=c, provider=p, prompt_tokens=100) for c, p in steps]
    )


def test_a_silent_route_is_none_not_zero() -> None:
    """None means "the route never said". Scoring it as 0 would read as a broken cache and invite
    someone to go fix a prefix that was never the problem — the exact trap `StepLog.cache_hit_rate`
    already documents."""
    assert _cache_read_tokens(_steplog((None, ""), (None, ""))) is None


def test_cache_tokens_sum_over_the_steps_that_reported() -> None:
    assert _cache_read_tokens(_steplog((0, "DeepSeek"), (1200, "DeepSeek"), (None, ""))) == 1200


def test_the_provider_is_the_first_route_that_named_itself() -> None:
    assert _provider(_steplog((None, ""), (5, "DeepSeek"), (5, "Other"))) == "DeepSeek"
    assert _provider(_steplog((None, ""))) == ""


def test_the_receipt_carries_both_fields_and_their_absence() -> None:
    known = Attempt(index=1, answer="a", approved=True, verified=True, reverted=False, success=True)
    known.cache_read_tokens = 1200
    known.provider = "DeepSeek"
    silent = Attempt(index=2, answer="b", approved=True, verified=True, reverted=False, success=True)

    receipt = build_receipt(
        AutonomousResult(answer="x", success=True, attempts=[known, silent]),
        "task", None, "2026-09-15T00:00:00+00:00",
    )
    assert receipt.attempts[0].cache_read_tokens == 1200
    assert receipt.attempts[0].provider == "DeepSeek"
    assert receipt.attempts[1].cache_read_tokens is None
    assert receipt.attempts[1].provider == ""


# --------------------------------------------------------------------- the route pin


def test_the_route_pin_is_off_by_default_and_turns_fallbacks_off_when_on() -> None:
    """With fallbacks on, an arm silently becomes whatever answered — the confound wearing the
    manipulation's name. So the pin is all-or-nothing: named routes, in order, no fallback."""
    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    assert "extra_body" not in LLMGateway(Settings())._provider_kwargs()
    pinned = LLMGateway(Settings(CHIMERA_PROVIDER_ORDER="DeepSeek, Novita"))._provider_kwargs()
    assert pinned["extra_body"] == {"provider": {"order": ["DeepSeek", "Novita"], "allow_fallbacks": False}}
