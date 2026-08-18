"""The list of models a request may name, and everything it refuses to make up.

Until this existed, naming a model was a memory test: a free-text box in Settings, a free-text box
in the wizard, and no way at all to name one from the conversation. The fix is a list — and a list
is exactly the kind of feature that lies quietly, so the assertions here are mostly about what does
NOT appear in it.

Three failures, each with its own test below:

- **Offering models the user's keys cannot call.** OpenRouter's index is public, so the easy version
  of this feature shows four hundred slugs to someone holding an Anthropic key. Every one of them
  answers 401, after they picked it.
- **Turning an unknown into a number.** OpenRouter quotes some models at request time and marks them
  ``"-1"``. Read as a float that is zero, or as a price at all, it becomes a free model in the menu
  and a division in a spend ceiling.
- **Reading absence as evidence.** A fetch that failed and a catalogue that is genuinely empty
  arrive at the UI identically unless the reason travels with them — and the curated list, which
  needs no network, has to survive a failure rather than be deleted by it.

Nothing here reaches the network: `httpx.get` is replaced. What is under test is how each answer is
READ, and a live catalogue would test whichever models happened to be published today.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from chimera.providers import listing
from chimera.providers.listing import ModelOption, available_models, openrouter_models


class _Settings:
    """The slice of Settings the listing needs, duck-typed like the tier resolver's."""

    def __init__(self, providers: list[str], *, ollama: str = "", default: str = "vendor/model"):
        self._providers = providers
        self.ollama_base_url = ollama
        self.default_model = default

    def configured_providers(self) -> list[str]:
        return list(self._providers)


class _Response:
    def __init__(self, status_code: int, payload: Any = None, *, valid_json: bool = True) -> None:
        self.status_code = status_code
        self._payload = payload
        self._valid_json = valid_json

    def json(self) -> Any:
        if not self._valid_json:
            raise ValueError("not json")
        return self._payload


def _entry(model_id: str, **over: Any) -> dict[str, Any]:
    """One OpenRouter index entry, in the shape their endpoint actually returns."""
    entry = {
        "id": model_id,
        "name": f"Vendor: {model_id}",
        "context_length": 128_000,
        "pricing": {"prompt": "0.00000025", "completion": "0.00000095"},
        "architecture": {"input_modalities": ["text"]},
        "supported_parameters": ["tools", "temperature"],
    }
    entry.update(over)
    return entry


def _answer(monkeypatch: pytest.MonkeyPatch, response: _Response | Exception) -> None:
    def fake_get(url: str, **kwargs: Any) -> _Response:
        assert url == listing.OPENROUTER_MODELS_URL, f"asked {url}, not the model index"
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(httpx, "get", fake_get)


@pytest.fixture(autouse=True)
def _no_cache_between_tests() -> Any:
    """The fetch is cached for an hour in production, which would make these tests read each other's
    answers. Cleared around every test rather than at the end of one, so a failure cannot leak."""
    listing._cache = None
    yield
    listing._cache = None


def test_the_index_is_read_into_options(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer(monkeypatch, _Response(200, {"data": [_entry("deepseek/deepseek-chat-v3.1")]}))

    models, reason = openrouter_models()

    assert reason == ""
    assert len(models) == 1
    found = models[0]
    # Prefixed, because LiteLLM routes on the first segment and the UI must never assemble a slug.
    assert found.slug == "openrouter/deepseek/deepseek-chat-v3.1"
    assert found.tools is True
    # Per-token decimals become dollars per million, which is the only unit anyone reads.
    assert found.input_per_m == 0.25
    assert found.output_per_m == 0.95
    assert found.context_k == 128


def test_a_price_quoted_at_request_time_is_unknown_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    # `-1` is OpenRouter's marker for "quoted per request". Zero would put "free" in the menu next
    # to a model that bills, and it is the number a spend ceiling would divide by.
    _answer(
        monkeypatch,
        _Response(200, {"data": [_entry("vendor/variable", pricing={"prompt": "-1", "completion": "-1"})]}),
    )

    models, _ = openrouter_models()

    assert models[0].input_per_m is None
    assert models[0].output_per_m is None
    assert models[0].free is False


def test_a_model_without_tool_support_says_so_and_one_that_is_silent_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # False and None are different answers: a coding turn on a model that cannot call tools produces
    # a description of an edit that never happened, and that warning is only worth showing when it
    # is not a guess.
    _answer(
        monkeypatch,
        _Response(
            200,
            {
                "data": [
                    _entry("vendor/no-tools", supported_parameters=["temperature"]),
                    _entry("vendor/unsaid", supported_parameters=None),
                ]
            },
        ),
    )

    models, _ = openrouter_models()

    assert models[0].tools is False
    assert models[1].tools is None


def test_free_tiers_are_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer(
        monkeypatch,
        _Response(200, {"data": [_entry("vendor/model:free", pricing={"prompt": "0", "completion": "0"})]}),
    )

    assert openrouter_models()[0][0].free is True


def test_vision_comes_from_the_modalities_not_from_the_vendor(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer(
        monkeypatch,
        _Response(
            200,
            {
                "data": [
                    _entry("vendor/sees", architecture={"input_modalities": ["text", "image"]}),
                    _entry("vendor/blind"),
                ]
            },
        ),
    )

    models, _ = openrouter_models()
    assert models[0].vision is True
    assert models[1].vision is False


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.ConnectError("no route"), "unreachable"),
        (_Response(500), "http_error"),
        (_Response(200, valid_json=False), "unreadable"),
        (_Response(200, {"data": "not a list"}), "unreadable"),
    ],
)
def test_every_failure_is_a_token_never_an_exception(
    monkeypatch: pytest.MonkeyPatch, response: Any, reason: str
) -> None:
    # This populates a menu. An exception here would turn "the catalogue is having a bad day" into a
    # 500 that reads as a bug in Chimera, and the reason is a WORD because the app ships ten
    # languages and English prose from the server is the one line the user cannot read.
    _answer(monkeypatch, response)

    models, got = openrouter_models()

    assert models == ()
    assert got == reason


def test_the_fetch_is_cached_rather_than_repeated(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def counting_get(url: str, **kwargs: Any) -> _Response:
        calls["n"] += 1
        return _Response(200, {"data": [_entry("vendor/model")]})

    monkeypatch.setattr(httpx, "get", counting_get)

    openrouter_models()
    openrouter_models()

    assert calls["n"] == 1


def test_a_remote_catalogue_is_not_offered_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # The failure this prevents: four hundred OpenRouter slugs offered to someone holding only an
    # Anthropic key, every one of which answers 401 *after* they picked it.
    def forbidden(url: str, **kwargs: Any) -> _Response:
        raise AssertionError("asked OpenRouter without an OpenRouter key")

    monkeypatch.setattr(httpx, "get", forbidden)

    found = available_models(_Settings(["anthropic"]))

    assert found.reason == "no_provider"
    assert all(m.source != "openrouter" for m in found.models)


def test_the_wizard_may_ask_about_a_key_it_has_not_saved_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    # The one moment where filtering by configured providers answers the wrong question: the user is
    # holding the key they are about to paste, and "what does this buy" is exactly what they asked.
    _answer(monkeypatch, _Response(200, {"data": [_entry("vendor/model")]}))

    found = available_models(_Settings([]), provider="openrouter")

    assert found.reason == ""
    assert any(m.source == "openrouter" for m in found.models)


def test_a_failed_fetch_leaves_the_curated_list_standing(monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty menu says "your key buys nothing", which is a claim about the user's account that a
    # network failure does not support. The curated models need no network and are still callable.
    _answer(monkeypatch, httpx.ConnectError("down"))

    found = available_models(_Settings(["openrouter"]))

    assert found.reason == "unreachable"
    assert found.models, "a failed remote fetch deleted the offline catalogue"
    assert all(m.recommended for m in found.models)


def test_a_curated_slug_the_live_index_dropped_is_not_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    # The catalogue says of itself that its slugs go stale. Offering a retired one is a 404 on the
    # first call, after the user chose it — and the live index is the only thing that can tell.
    from chimera.providers.catalog import CATALOG

    alive = next(e.slug for e in CATALOG if e.slug.startswith("openrouter/"))
    _answer(monkeypatch, _Response(200, {"data": [_entry(alive.removeprefix("openrouter/"))]}))

    found = available_models(_Settings(["openrouter"]))
    slugs = {m.slug for m in found.models}

    assert alive in slugs
    retired = {e.slug for e in CATALOG if e.slug.startswith("openrouter/")} - {alive}
    assert not (retired & slugs), "a slug the live index no longer carries is still on offer"


def test_the_live_entry_wins_on_price_and_keeps_the_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A model in both catalogues is ONE row: the live price (the curated numbers are documented as
    # approximate and stale) with the recommendation that made us list it in the first place.
    from chimera.providers.catalog import CATALOG

    curated = next(e for e in CATALOG if e.slug.startswith("openrouter/") and e.input_per_m)
    _answer(
        monkeypatch,
        _Response(
            200,
            {
                "data": [
                    _entry(
                        curated.slug.removeprefix("openrouter/"),
                        pricing={"prompt": "0.00001", "completion": "0.00002"},
                    )
                ]
            },
        ),
    )

    found = available_models(_Settings(["openrouter"]))
    merged = next(m for m in found.models if m.slug == curated.slug)

    assert merged.recommended is True
    assert merged.input_per_m == 10.0
    assert merged.input_per_m != curated.input_per_m


def test_recommended_models_come_first(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.providers.catalog import CATALOG

    curated = next(e.slug for e in CATALOG if e.slug.startswith("openrouter/"))
    _answer(
        monkeypatch,
        _Response(
            200,
            {"data": [_entry("vendor/unrelated"), _entry(curated.removeprefix("openrouter/"))]},
        ),
    )

    models = available_models(_Settings(["openrouter"])).models

    assert models[0].slug == curated
    assert models[0].recommended is True


def test_local_models_are_listed_and_priced_at_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    # The one price we can state without asking anyone: a model running on this machine bills
    # nothing per token.
    monkeypatch.setattr(
        listing,
        "_ollama_options",
        lambda base: [ModelOption(slug="ollama/llama3", label="llama3", vendor="Ollama", source="ollama", free=True)],
    )
    _answer(monkeypatch, _Response(200, {"data": []}))

    models = available_models(_Settings(["openrouter"], ollama="http://localhost:11434")).models

    assert any(m.slug == "ollama/llama3" and m.free for m in models)


def test_the_endpoint_reports_the_default_next_to_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """The picker marks the default rather than guessing it, so the response has to carry it.

    Without this field the UI knows what you can pick and not what happens if you pick nothing —
    and "nothing chosen" is the state every conversation starts in.
    """
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.interface import ChatSession

    _answer(monkeypatch, _Response(200, {"data": [_entry("vendor/model")]}))
    client = TestClient(build_api_app(lambda: ChatSession(None)))  # type: ignore[arg-type]

    body = client.get("/api/models").json()

    assert body["default"], "the response does not say what runs when nothing is picked"
    assert isinstance(body["models"], list)
    assert body["reason"] in {"", "no_provider", "unreachable", "http_error", "unreadable"}
