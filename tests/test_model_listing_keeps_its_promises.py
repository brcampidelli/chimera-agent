"""Two promises the model picker's own docstrings make, and did not keep.

**`?provider=` was a no-op.** Its documented job is the onboarding wizard's: answer *what does this
key buy* while holding a key that has not been saved yet, so nothing is configured and the override
is the only thing to go on. The code compared it against the literal `"openrouter"` and nothing
else, and then built the curated list from `settings.configured_providers()` — the very set the
override exists to stand in for. Four different values produced four byte-identical bodies.

**The Ollama probe took twice its documented budget.** The module says a settings row must not
freeze, and names two seconds. `localhost` resolves to both `::1` and `127.0.0.1`, neither refuses
when nothing is listening, and httpx tries them in turn with the full timeout each: 2 + 2 = 4.4s
measured. That cost is paid on every machine WITHOUT Ollama, which is most of them, and it is paid
by the model picker as a whole because listing calls the probe.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from chimera.config import Settings
from chimera.providers import ollama
from chimera.providers.listing import available_models


def _no_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """No network in these tests: the question is which CURATED models are offered."""
    monkeypatch.setattr(
        "chimera.providers.listing.openrouter_models", lambda **_: ((), "unreachable")
    )
    monkeypatch.setattr(
        "chimera.providers.listing._ollama_options", lambda *a, **k: ()
    )


def _slug_prefixes(options: Any) -> set[str]:
    return {option.slug.split("/", 1)[0] for option in options}


def test_naming_a_provider_with_no_catalogue_offers_nothing(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect, stated against what the catalogue actually holds.

    All fifteen curated entries are `openrouter/...` slugs, because that is what this install
    routes through — so there is no Anthropic list to show and the honest answer is an empty one.
    What it USED to answer was the whole OpenRouter catalogue: 422 slugs, none of them Anthropic,
    under a question that named Anthropic.

    My first version of this test asserted that naming Anthropic should show Anthropic models. It
    could never pass, and it was asserting about a catalogue this project does not have.
    """
    _no_remote(monkeypatch)
    settings = Settings(OPENROUTER_API_KEY="sk-test")

    listed = available_models(settings, provider="anthropic").models

    assert list(listed) == []


def test_a_provider_nobody_has_heard_of_offers_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Not an error — this is a picker, and an empty picker says "nothing here". Answering a
    # nonsense provider with every model there is was how the no-op hid: it looked like a default.
    _no_remote(monkeypatch)
    settings = Settings(OPENROUTER_API_KEY="sk-test")

    listed = available_models(settings, provider="definitely-not-a-provider").models

    assert list(listed) == []


def test_naming_openrouter_works_without_a_key_being_saved(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    # The wizard's actual case, and the reason the argument exists: a key in hand, nothing saved,
    # "what does this buy me?". This is what must keep working, and a fix that made every named
    # provider empty would break it.
    monkeypatch.setattr(
        "chimera.providers.listing.openrouter_models", lambda **_: ((), "unreachable")
    )
    monkeypatch.setattr("chimera.providers.listing._ollama_options", lambda *a, **k: ())
    settings = Settings(OPENROUTER_API_KEY=None)

    listed = available_models(settings, provider="openrouter").models

    assert listed, "the one provider with a catalogue must still answer"
    assert _slug_prefixes(listed) == {"openrouter"}


def test_asking_for_nothing_still_uses_the_configured_keys(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    # The path everything else in the app takes. A fix that made the override authoritative even
    # when absent would empty the picker for every existing user.
    _no_remote(monkeypatch)
    settings = Settings(OPENROUTER_API_KEY="sk-test")

    listed = available_models(settings).models

    assert _slug_prefixes(listed) == {"openrouter"}


def test_the_ollama_probe_stays_inside_its_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe that overruns its budget is abandoned AT the budget.

    Driven by a stub that simply takes too long, rather than by pointing at a real address. The
    real cause is `localhost` resolving to both `::1` and `127.0.0.1` while neither refuses, so
    httpx pays the full timeout twice — measured live at 4.4s against a documented 2.0s. But a test
    that reproduced it that way would be asserting about this machine's network stack: a port that
    refuses quickly makes it pass while the defect is still there, which is what the first version
    of this test did.
    """
    import httpx

    def _too_slow(*_a: Any, **_k: Any) -> Any:
        time.sleep(5.0)
        raise AssertionError("should have been abandoned long before this")

    monkeypatch.setattr(httpx, "get", _too_slow)

    began = time.monotonic()
    result = ollama.installed_models("http://localhost:11434", timeout_s=1.0)
    elapsed = time.monotonic() - began

    assert result.reachable is False
    assert result.reason == "unreachable"
    assert elapsed < 2.0, f"took {elapsed:.2f}s against a 1.0s budget"


def test_a_reachable_ollama_still_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    # The deadline must bound the call, not replace it. Without this, "return unreachable
    # immediately" would pass the test above and break every machine that HAS Ollama.
    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"models": [{"name": "llama3:8b"}]}

    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Response())

    result = ollama.installed_models("http://localhost:11434", timeout_s=2.0)

    assert result.reachable is True
    assert list(result.models) == ["llama3:8b"]
